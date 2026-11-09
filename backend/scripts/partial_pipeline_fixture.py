"""Авторская сборка известных synthetic requests; runtime её не импортирует."""

from dataclasses import replace
import json
from threading import Event

from app.assessment_schemas import NeedAssessment
from app.gateways import GatewayResult
from app.partial_integration import build_partial_pipeline, build_partial_profile
from app.pipeline import Pipeline
from app.replay import make_record, publish_records
from app.schemas import DecisionUpdate, ExtractedNeed, ExtractedNeeds, Run, RunRequest
from app.clock import now
from scripts.partial_media_fixture import prepare_media_demo
from scripts.text_pipeline_fixture import AuthoredFixtureGateway


class AuthoredPartialGateway(AuthoredFixtureGateway):
    def generate(self, request, response_schema):
        if request.operation == "extract_needs":
            doc = request.data["document"]
            assert doc["source_id"] == "SYNTHETIC-MEDIA-1"
            value = ExtractedNeeds(needs=[ExtractedNeed(need_id="pending", source_id=doc["source_id"],
                fragment_id=f["fragment_id"], evidence_quote=f["text"], scenario="Контакт заявки",
                need_type="operational_request", statement="Синтетическая потребность изменения контакта", explicit_request=True)
                for f in doc["fragments"] if f["text"].strip()])
        elif request.operation == "classify_need":
            n = request.data["need"]
            value = NeedAssessment(category="operational_request", priority="unknown",
                reason="Авторский synthetic пример; влияние и срочность не установлены.",
                uncertainties=["Нет подтверждения влияния на работу пользователя."],
                evidence_refs=[dict(source_id=n["source_id"], fragment_id=n["fragment_id"], quote=n["evidence_quote"])])
        else:
            return super().generate(request, response_schema)
        record = make_record(request, response_schema, value.model_dump(mode="json"), model_id=self.profile.model_id,
            normalization_hash=self.profile.parameters["normalization_hash"], set_version=self.profile.recording_set_version, origin="authored")
        self.records.append(record)
        return GatewayResult(value=value, metadata=record.metadata)


def damaged_png(demo):
    material = next(m for m in demo.repo.source_materials(demo.ticket.source_id) if m.mime_type == "image/png")
    return demo.settings.app_upload_root / material.storage_path


def run_sync(repo, adapters, run_id):
    sources = repo.select_run_sources(RunRequest())
    run = Run(run_id=run_id, mode="replay", state="queued", stage="queued", created_at=now(),
              total_materials=sum(len(s.ticket.assets) + int(bool(s.ticket.text) or not s.ticket.assets) for s in sources), coverage=0.)
    with repo.transaction() as tx:
        tx.create_run(run)
        tx.save_run_inputs(run_id, RunRequest(), adapters.profile, sources)
    Pipeline(repo, adapters, Event()).execute(run_id)
    return repo.get_run(run_id)


def save_fixture_decision(repo):
    cards = repo.list_topics("replay", support_level="all")
    assert len(cards) == 1
    card = cards[0]
    repo.record_decision(card.topic.topic_id, DecisionUpdate(status="needs_research", owner="Синтетический владелец",
        reason="Сохранить решение при дополнении media context.", version=card.topic.version))
    return repo.get_topic(card.topic.topic_id, "replay")


def prepare_pipeline_demo(folder):
    demo = prepare_media_demo(folder)
    training = prepare_media_demo(folder / "recording-build")
    profile = build_partial_profile(demo.settings, media_profile=demo.profile,
        model_id="synthetic-text-no-live-model", recording_set_version="synthetic-partial-text-v1")
    authored = AuthoredPartialGateway(profile)
    training_settings = training.settings.model_copy(update={"app_media_recordings_path": training.recordings,
        "app_media_cache_root": training.folder / "cache"})
    adapters = build_partial_pipeline(authored, profile, training_settings, training.repo)
    path = damaged_png(training)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        first = run_sync(training.repo, adapters, "authored-partial")
        assert first.state == "completed_with_errors", first.model_dump()
        assert not [e for e in first.errors if e.stage in {"extraction", "assignment", "classification", "processing"}], first.errors
        save_fixture_decision(training.repo)
    finally:
        path.write_bytes(raw)
    second = run_sync(training.repo, adapters, "authored-complete")
    assert second.state == "completed", second.model_dump()
    recordings = folder / "text-recordings"
    publish_records(recordings, authored.records)
    profile_path = folder / "processing-profile.json"
    profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    settings = demo.settings.model_copy(update={"app_processing_profile_path": profile_path,
        "app_replay_model_id": profile.model_id, "app_replay_normalization_hash": profile.parameters["normalization_hash"],
        "app_recordings_root": recordings, "app_media_recordings_path": demo.recordings,
        "app_media_cache_root": folder / "cache"})
    return replace(demo, settings=settings), profile
