"""Изолированный разнообразный synthetic/authored показ; никакого live gateway."""

from dataclasses import replace
import json
from uuid import uuid4

from app.assessment_schemas import NeedAssessment
from app.bootstrap import configured_text_pipeline
from app.config import REPO_ROOT
from app.gateways import GatewayResult, text_gateway
from app.ingestion.importer import import_tickets
from app.partial_integration import build_partial_pipeline, build_partial_profile
from app.replay import make_record, publish_records
from app.schemas import BatchAssignment, DecisionUpdate, ExtractedNeed, ExtractedNeeds, PassportExample, ProposedTopic, TicketInput
from scripts.media_import_fixture import synthetic_files
from scripts.partial_media_fixture import prepare_media_demo
from scripts.partial_pipeline_fixture import AuthoredPartialGateway, damaged_png, run_sync


CASES = {
    "ACCEPT-INVOICE-1": ("При сохранении счёта появляется ошибка, приходится вводить данные повторно.",
        "Сохранение счёта", "Сохранять счёт без повторного ввода после ошибки", "suspected_defect", "elevated"),
    "ACCEPT-INVOICE-2": ("После ошибки сохранения счёта введённые данные теряются, приходится вводить заново.",
        "Сохранение счёта", "Сохранять счёт без повторного ввода после ошибки", "suspected_defect", "elevated"),
    "ACCEPT-EXPORT-1": ("Прошу добавить экспорт списка заявок в CSV для еженедельной сверки.",
        "Сверка заявок", "Выгружать список заявок в CSV", "improvement_request", "normal"),
    "ACCEPT-UNCLEAR-1": ("Прошу сделать как раньше. Не могу уточнить экран и действие.",
        "Неуточнённый сценарий", "Уточнить ожидаемое поведение неизвестного экрана", "unclassified", "unknown"),
}


class AcceptanceAuthoredGateway(AuthoredPartialGateway):
    """Только построение известных записей. Обычный backend использует ReplayGateway."""

    def generate(self, request, response_schema):
        source_id = request.data.get("document", {}).get("source_id") if request.operation == "extract_needs" else request.data["need"]["source_id"]
        if source_id not in CASES:
            return super().generate(request, response_schema)
        text, scenario, statement, category, priority = CASES[source_id]
        if request.operation == "extract_needs":
            fragment = request.data["document"]["fragments"][0]
            if fragment["text"] != text:
                raise ValueError("Unknown authored acceptance input")
            value = ExtractedNeeds(needs=[ExtractedNeed(need_id="pending", source_id=source_id,
                fragment_id=fragment["fragment_id"], evidence_quote=text, scenario=scenario,
                statement=statement, need_type="problem" if category == "suspected_defect" else "feature_request",
                explicit_request=category != "suspected_defect")])
        else:
            need = request.data["need"]
            ref = dict(source_id=source_id, fragment_id=need["fragment_id"], quote=need["evidence_quote"])
            if request.operation == "classify_need":
                value = NeedAssessment(category=category, priority=priority,
                    reason={"elevated": "Описаны потеря введённых данных и повторный ввод; масштаб не подтверждён.",
                        "normal": "Описана регулярная сверка без оснований для повышения срочности.",
                        "unknown": "Не указан экран и ожидаемое действие."}[priority],
                    uncertainties=["Нет данных о масштабе и влиянии."] if priority != "normal" else [], evidence_refs=[ref])
            elif request.operation == "assign_topic":
                topics = request.data["topics"]
                ids = [t["topic_id"] for t in topics if t["passport"]["need_statement"] == statement]
                decision = "review" if category == "unclassified" else "match" if ids else "provisional_no_match"
                value = BatchAssignment(decision=decision, matched_topic_id=ids[0] if decision == "match" else None,
                    candidate_topic_ids=ids, reason="Явно подготовленный synthetic/authored пример; не качество модели.", evidence_refs=[ref])
            else:
                if request.operation != "propose_topic" or category == "unclassified":
                    raise ValueError("Unsupported acceptance operation")
                value = ProposedTopic(title="Синтетическая тема: " + scenario, summary=statement,
                    scenario=scenario, need_statement=statement, scope="Только описанное в синтетических источниках действие",
                    inclusion_examples=[PassportExample(text=need["evidence_quote"], evidence_refs=[ref])])
        record = make_record(request, response_schema, value.model_dump(mode="json"), model_id=self.profile.model_id,
            normalization_hash=self.profile.parameters["normalization_hash"], set_version=self.profile.recording_set_version, origin="authored")
        self.records.append(record)
        return GatewayResult(value=value, metadata=record.metadata)


def add_text_sources(demo):
    tickets = [TicketInput(source_id=source_id, created_at=f"2026-11-0{3 + i}T12:00:00+03:00", text=case[0])
        for i, (source_id, case) in enumerate(CASES.items())]
    manifest = dict(dataset_id="synthetic-acceptance-text-v1", synthetic=True,
        tickets=[t.model_dump(mode="json") for t in tickets])
    assert import_tickets(json.dumps(manifest).encode(), [], repository=demo.repo).counts.inserted == 4
    return manifest


def save_contact_decision(demo):
    cards = demo.repo.list_topics("replay", support_level="all")
    contact = next(c for c in cards if "изменения контакта" in c.topic.title)
    return demo.repo.record_decision(contact.topic.topic_id, DecisionUpdate(version=contact.topic.version,
        status="needs_research", owner="Синтетический владелец", reason="Проверить сохранность при повторе partial → complete."))


def prepare_acceptance_demo(folder):
    demo = prepare_media_demo(folder)
    manifest = add_text_sources(demo)
    training = prepare_media_demo(folder / "recording-build")
    add_text_sources(training)
    profile = build_partial_profile(demo.settings, media_profile=demo.profile,
        model_id="synthetic-acceptance-no-live-model", recording_set_version="synthetic-acceptance-v1")
    authored = AcceptanceAuthoredGateway(profile)
    training_settings = training.settings.model_copy(update={"app_media_recordings_path": training.recordings,
        "app_media_cache_root": training.folder / "cache"})
    adapters = build_partial_pipeline(authored, profile, training_settings, training.repo)
    path = damaged_png(training)
    original = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        assert run_sync(training.repo, adapters, "authored-partial").state == "completed_with_errors"
        save_contact_decision(training)
    finally:
        path.write_bytes(original)
    assert run_sync(training.repo, adapters, "authored-full").state == "completed"
    records_root = folder / "text-recordings"
    publish_records(records_root, authored.records)
    profile_path = folder / "processing-profile.json"
    profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    settings = demo.settings.model_copy(update={"app_processing_profile_path": profile_path,
        "app_replay_model_id": profile.model_id, "app_replay_normalization_hash": profile.parameters["normalization_hash"],
        "app_recordings_root": records_root, "app_media_recordings_path": demo.recordings,
        "app_media_cache_root": folder / "cache"})
    demo = replace(demo, settings=settings)
    replay, error = configured_text_pipeline(settings, text_gateway(settings))
    assert replay is not None and error is None
    path = damaged_png(demo)
    original = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        first = run_sync(demo.repo, replay, "acceptance-partial")
        assert first.state == "completed_with_errors" and first.material_coverage.successful == 8
        save_contact_decision(demo)
    finally:
        path.write_bytes(original)
    selected = folder / "selected-files"
    selected.mkdir()
    for name, raw in synthetic_files().items():
        (selected / name).write_bytes(raw)
    (selected / "manifest.json").write_text(json.dumps(dict(dataset_id="synthetic-partial-media-v1", synthetic=True,
        tickets=[demo.ticket.model_dump(mode="json")]), ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "text-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return demo, profile


def main():
    folder = REPO_ROOT / "var" / ("acceptance-demo-" + uuid4().hex)
    demo, profile = prepare_acceptance_demo(folder)
    env = dict(APP_DB_PATH=str(demo.repo.path), APP_UPLOAD_ROOT=str(demo.settings.app_upload_root), APP_TMP_ROOT=str(demo.settings.app_tmp_root),
        APP_MODEL_MODE="replay", APP_PROCESSING_PROFILE_PATH=str(demo.settings.app_processing_profile_path),
        APP_RECORDINGS_ROOT=str(demo.settings.app_recordings_root), APP_MEDIA_RECORDINGS_PATH=str(demo.recordings),
        APP_MEDIA_CACHE_ROOT=str(demo.settings.app_media_cache_root), APP_REPLAY_MODEL_ID=profile.model_id,
        APP_REPLAY_NORMALIZATION_HASH=profile.parameters["normalization_hash"], TEXT_MODEL_BASE_URL="", TEXT_MODEL_ID="",
        ASR_BASE_URL="", ASR_MODEL_ID="", VL_BASE_URL="", VL_MODEL_ID="")
    result = dict(synthetic=True, origin="authored", live_model_calls=False, folder=str(folder),
        selected_files=str(folder / "selected-files"), text_manifest=str(folder / "text-manifest.json"),
        sources=5, topics=3, recurring_topic_sources=2, material_coverage="8/9 before retry; 9/9 after retry", environment=env)
    (folder / "demo-settings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
