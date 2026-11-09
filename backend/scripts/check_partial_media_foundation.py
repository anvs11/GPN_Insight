"""A1–A3: isolated actual import/recordings/partial retry, не production pipeline."""

from hashlib import sha256
import json
from uuid import uuid4

from app.config import REPO_ROOT
from app.ingestion.media_normalizer import MediaNormalizer
from app.media_replay import MediaReplayGateway
from app.processing_schemas import NormalizationResult, analysis_input
from app.repositories import Repository
from app.schemas import DecisionUpdate
from scripts.media_import_fixture import seed_authored_topic
from scripts.partial_media_fixture import prepare_media_demo


def main() -> None:
    private = [REPO_ROOT / ".env", *sorted((REPO_ROOT / "var").glob("*.db"))]
    protected = {path: sha256(path.read_bytes()).hexdigest() for path in private if path.is_file()}
    folder = REPO_ROOT / "var" / ("partial-media-check-" + uuid4().hex)
    demo = prepare_media_demo(folder)
    topic_id = seed_authored_topic(demo.repo)  # Явно authored grounding только по тексту.
    demo.repo.record_decision(topic_id, DecisionUpdate(version=1, status="needs_research", owner="Синтетический владелец", reason="Сохранить решение при media retry"))
    card = demo.repo.get_topic(topic_id, "replay")
    before = demo.repo.path.read_bytes()
    material = next(m for m in demo.repo.source_materials(demo.ticket.source_id) if m.mime_type == "image/png")
    path = demo.settings.app_upload_root / material.storage_path
    raw = path.read_bytes()
    gateway = MediaReplayGateway(demo.recordings, demo.profile, allowed_root=demo.settings.app_upload_root)
    calls = []
    class Counted:
        def transcribe(self, asset):
            calls.append(asset.asset_id)
            return gateway.transcribe(asset)
        def describe(self, asset):
            calls.append(asset.asset_id)
            return gateway.describe(asset)
    counted = Counted()
    def normalize():
        source = demo.repo.get_source(demo.ticket.source_id)
        service = MediaNormalizer(Repository(demo.repo.path), demo.settings, demo.profile,
            asr_gateway=counted, vision_gateway=counted, cache_root=folder / "success-cache")
        return service.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=source.source_version)
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        partial = normalize()
        assert partial.state == "partial" and partial.coverage.successful == 4 and partial.coverage.failed == 1
        view = analysis_input(partial)
        assert view is not None and view.source_errors == partial.document.errors and not view.document.errors
        assert len(calls) == 3
        snapshot = folder / "partial-result.json"
        snapshot.write_text(partial.model_dump_json(indent=2), encoding="utf-8")
    finally:
        path.write_bytes(raw)
    complete = normalize()
    assert complete.state == "complete" and complete.coverage.successful == 5 and len(calls) == 4
    assert set(f.fragment_id for f in partial.document.fragments) <= set(f.fragment_id for f in complete.document.fragments)
    assert partial.revision_id != complete.revision_id and partial.analysis_digest != complete.analysis_digest
    assert NormalizationResult.model_validate_json(snapshot.read_text(encoding="utf-8")) == partial
    assert normalize() == complete and len(calls) == 4
    assert demo.repo.path.read_bytes() == before and Repository(demo.repo.path).get_topic(topic_id, "replay") == card
    assert all(sha256(path.read_bytes()).hexdigest() == value for path, value in protected.items())
    (folder / "complete-result.json").write_text(complete.model_dump_json(indent=2), encoding="utf-8")
    result = {"synthetic": True, "origins": ["authored"], "live_model_calls": False, "network_used": False,
        "materials": 5, "first_success": 4, "first_failed": 1, "final_success": 5, "gateway_calls": len(calls),
        "successful_media_reused": True, "stable_fragments": True, "old_revision_preserved": True,
        "analysis_digest_changed": True, "sqlite_and_decisions_unchanged": True, "private_files_preserved": True,
        "production_media_pipeline_connected": False}
    (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("synthetic=true; formats=WAV/MP3/PNG/JPEG; origins=authored; live_model_calls=false; network_used=false")
    print("partial: success=4/5; failed=1; usable context available; original errors retained")
    print("retry/reopen: success=5/5; gateway calls=3+1; stable fragments; new revision/digest; old snapshot valid")
    print("PASS: SQLite/decision/history unchanged; private files preserved; production media pipeline not connected")
    print("report:", (folder / "result.json").relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
