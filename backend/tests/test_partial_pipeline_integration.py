from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

from fastapi.testclient import TestClient
import pytest

from app.bootstrap import configured_text_pipeline
from app.clock import fixed_clock
from app.db import connection, initialize
from app.errors import RepositoryError
from app.gateways import text_gateway
from app.main import create_app
from app.processing_schemas import analysis_input
from app.repositories import Repository
from app.schemas import DecisionUpdate, ManualOverride
from app.weekly import completed_week
from scripts.partial_pipeline_fixture import damaged_png, prepare_pipeline_demo, run_sync, save_fixture_decision


def test_real_bootstrap_partial_retry_reopen_preserves_decision_and_reuses_steps(tmp_path):
    demo, profile = prepare_pipeline_demo(tmp_path)
    adapters, error = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    assert error is None and adapters.partial
    path = damaged_png(demo)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        first = run_sync(demo.repo, adapters, "partial")
        assert first.state == "completed_with_errors"
        assert first.material_coverage.successful == 4 and first.source_coverage.partial == 1
        original = demo.repo.processing_result(demo.ticket.source_id)
        assert original.state == "partial" and not demo.repo.processing_pending(demo.ticket.source_id)
        saved = save_fixture_decision(demo.repo)
        old_needs = set(saved.topic.need_ids)
    finally:
        path.write_bytes(raw)
    second = run_sync(demo.repo, adapters, "complete")
    assert second.state == "completed", second.model_dump()
    current = demo.repo.get_topic(saved.topic.topic_id, "replay")
    assert current.topic.status == saved.topic.status and current.topic.owner == saved.topic.owner
    assert current.history == saved.history and current.topic.passport == saved.topic.passport
    assert old_needs < set(current.topic.need_ids) and current.stats.ticket_count == 1
    assert demo.repo.processing_result(demo.ticket.source_id).analysis_digest != original.analysis_digest
    assert demo.repo.processing_result(demo.ticket.source_id, revision_id=original.revision_id) == original
    fresh = Repository(demo.repo.path)
    again, error = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    assert error is None
    third = run_sync(fresh, again, "repeat")
    assert third.state == "completed" and third.completed_steps == 0 and third.reused_steps > 0
    assert fresh.get_topic(saved.topic.topic_id, "replay") == current
    with connection(fresh.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("instant,start,end", [
    ("2026-11-09T00:00:00+03:00", "2026-11-02", "2026-11-08"),
    ("2026-11-08T20:59:59+00:00", "2026-10-26", "2026-11-01"),
    ("2026-11-08T21:00:00+00:00", "2026-11-02", "2026-11-08"),
    ("2027-01-01T12:00:00+03:00", "2026-12-21", "2026-12-27"),
    ("2024-03-04T12:00:00+03:00", "2024-02-26", "2024-03-03"),
])
def test_completed_week_moscow_boundaries(instant, start, end):
    week = completed_week(datetime.fromisoformat(instant))
    assert week.period.date_from.isoformat() == start and week.period.date_to.isoformat() == end
    assert (week.end_utc_exclusive - week.start_utc).total_seconds() == 7 * 86400
    assert week.start_utc.hour == 21
    with pytest.raises(ValueError): completed_week(datetime(2026, 11, 9))


def test_media_exact_bytes_ranges_and_access_policy(tmp_path):
    demo, _ = prepare_pipeline_demo(tmp_path)
    with TestClient(create_app(demo.settings), base_url="http://127.0.0.1") as client:
        prefix = f"/api/v1/sources/{demo.ticket.source_id}/versions/1/assets/"
        for asset in demo.ticket.assets:
            url = prefix + asset.asset_id
            result = client.get(url)
            assert result.status_code == 200 and result.headers["content-type"] == asset.mime_type
            assert result.headers["x-content-type-options"] == "nosniff" and result.headers["cache-control"] == "no-store"
            assert "uploads" not in result.headers["content-disposition"]
            head = client.head(url)
            assert head.status_code == 200 and not head.content and int(head.headers["content-length"]) == len(result.content)
            if asset.kind == "audio":
                for header, expected in [("bytes=0-9", result.content[:10]), ("bytes=-9", result.content[-9:]), ("bytes=9-", result.content[9:])]:
                    part = client.get(url, headers={"Range": header})
                    assert part.status_code == 206 and part.content == expected and "content-range" in part.headers
            else:
                assert client.get(url, headers={"Range": "bytes=0-9"}).status_code == 416
        url = prefix + demo.ticket.assets[0].asset_id
        for header in ("bytes=999999999-", "bytes=0-1,2-3", "bytes=-0", "bytes=9-1", "items=0-1"):
            assert client.get(url, headers={"Range": header}).status_code == 416
        assert client.get(prefix + "foreign").status_code == 404
        assert client.get(url.replace("/versions/1/", "/versions/2/")).status_code == 404
        assert client.get(url + "?path=secret").status_code == 422
        assert client.get(url, headers={"Range": "bytes=0-9", "If-Range": '"unknown"'}).status_code == 200
        material = demo.repo.source_materials(demo.ticket.source_id)[0]
        physical = demo.settings.app_upload_root / material.storage_path
        saved = physical.read_bytes()
        try:
            physical.write_bytes(b"SYNTHETIC-BROKEN")
            broken_url = prefix + material.asset_id
            failure = client.get(broken_url)
            assert failure.status_code == 503 and str(demo.folder) not in failure.text
        finally:
            physical.write_bytes(saved)
        with connection(demo.repo.path) as db:
            db.execute("UPDATE source_records SET synthetic=0")
        assert client.get(url).status_code == 403


def test_v3_migration_backup_repeat_rollback_and_preservation(tmp_path, fixture, monkeypatch):
    from app import db as database
    path = tmp_path / "old.db"
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    before = repo.get_topic(fixture.topic.topic_id, "replay")
    with connection(path) as db:
        for table in ("report_snapshots", "need_assessments", "processing_needs", "processing_heads", "processing_results"):
            db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE schema_version SET version=3")
    original = database.migrate_v3
    def fail(db):
        original(db)
        raise RuntimeError("injected failure")
    monkeypatch.setattr(database, "migrate_v3", fail)
    with pytest.raises(RuntimeError): initialize(path)
    with connection(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='processing_results'").fetchone()
    monkeypatch.setattr(database, "migrate_v3", original)
    result = initialize(path)
    assert result.version == 5 and result.migrated_from == 3 and result.backup_path.exists()
    with connection(result.backup_path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    assert Repository(path).get_topic(fixture.topic.topic_id, "replay") == before
    assert initialize(path).backup_path is None


def test_publication_rollback_then_retirement_keeps_manual_history_and_evidence(tmp_path, monkeypatch):
    demo, _ = prepare_pipeline_demo(tmp_path)
    adapters, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    path = damaged_png(demo)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        run_sync(demo.repo, adapters, "first")
        saved = save_fixture_decision(demo.repo)
    finally:
        path.write_bytes(raw)
    need_id = saved.topic.need_ids[0]
    demo.repo.record_override(ManualOverride(override_id="synthetic-manual", source_id=demo.ticket.source_id,
        source_version=1, need_id=need_id, topic_id=saved.topic.topic_id, action="attach", author="Аналитик",
        reason="Синтетическая ручная связь", created_at=datetime.now(timezone.utc)),
        expected_versions={saved.topic.topic_id: saved.topic.version})
    before = demo.repo.get_topic(saved.topic.topic_id, "replay")
    source = demo.repo.get_source(demo.ticket.source_id)
    result = adapters.normalizer.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=1)
    # Нормализация отдельно публикуется, но прежний анализ явно pending.
    demo.repo.publish_normalization(result)
    assert demo.repo.get_source(demo.ticket.source_id).analysis_pending
    assert demo.ticket.source_id in demo.repo.get_topic(saved.topic.topic_id, "replay").pending_source_ids
    assert not demo.repo.get_overrides(demo.ticket.source_id)[0].requires_recheck
    original_change = Repository._change
    def fail(*args, **kwargs): raise RuntimeError("injected publication failure")
    monkeypatch.setattr(Repository, "_change", staticmethod(fail))
    with pytest.raises(RuntimeError): demo.repo.publish_processing_needs(result, [])
    assert demo.repo.get_topic(saved.topic.topic_id, "replay").history == before.history
    assert set(demo.repo.get_topic(saved.topic.topic_id, "replay").topic.need_ids) == set(before.topic.need_ids)
    assert not demo.repo.get_overrides(demo.ticket.source_id)[0].requires_recheck
    monkeypatch.setattr(Repository, "_change", staticmethod(original_change))
    demo.repo.publish_processing_needs(result, [])
    fresh = Repository(demo.repo.path)
    after = fresh.get_topic(saved.topic.topic_id, "replay")
    assert after.topic.status == before.topic.status and after.topic.owner == before.topic.owner
    assert after.history == before.history and after.topic.passport == before.topic.passport
    assert after.stats.ticket_count == 0 and fresh.get_overrides(demo.ticket.source_id)[0].requires_recheck
    assert demo.ticket.source_id in after.pending_source_ids and fresh.get_source(demo.ticket.source_id).manual_recheck_required
    assert not fresh.get_source(demo.ticket.source_id).needs and not fresh.get_source(demo.ticket.source_id).analysis_pending
    # Исторические evidence остаются пригодны для проверки неизменённого паспорта.
    with connection(fresh.path) as db:
        assert db.execute("SELECT active FROM stored_needs WHERE need_id=?", (need_id,)).fetchone()[0] == 0
        assert db.execute("SELECT 1 FROM normalized_fragments WHERE fragment_id=?", (before.topic.evidence[0].fragment_id,)).fetchone()


def test_weekly_http_coverage_assessments_and_legacy_unknown(tmp_path):
    demo, _ = prepare_pipeline_demo(tmp_path)
    adapters, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    path = damaged_png(demo)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        run_sync(demo.repo, adapters, "partial")
    finally:
        path.write_bytes(raw)
    with fixed_clock(datetime.fromisoformat("2026-11-09T12:00:00+03:00")):
        with TestClient(create_app(demo.settings), base_url="http://127.0.0.1") as client:
            health = client.get("/api/v1/health").json()
            assert health["pipeline_ready"] and health["available_models"]["asr"]["code"] == "MEDIA_REPLAY_READY"
            period = client.get("/api/v1/reports/weekly/period").json()
            assert period["period"]["date_from"] == "2026-11-02"
            report = client.get("/api/v1/reports/weekly")
            assert report.status_code == 200 and "2026-11-08" in report.text
            assert "Уникальных источников в периоде: 1." in report.text
            assert "category=operational_request: 1" in report.text and "priority=unknown: 1" in report.text
            assert "sources/partial: 1" in report.text and "failed=1" in report.text
            assert "завершённая неделя=1; предыдущая=0" in report.text
            assert demo.ticket.text not in report.text and "live" in report.text
            assert client.get("/api/v1/reports/weekly?date_from=2026-01-01").status_code == 422
            result = client.get(f"/api/v1/sources/{demo.ticket.source_id}/processing").json()
            assert result["result"]["state"] == "partial" and not result["unassessed_need_ids"]
            card = demo.repo.list_topics("replay", support_level="all")[0]
            summary = client.get(f"/api/v1/topics/{card.topic.topic_id}/assessment-summary").json()
            assert summary["source_count"] == 1 and summary["categories"] == {"operational_request": 1}
            assert summary["priorities"] == {"unknown": 1}


def test_unknown_classifier_record_is_visible_and_does_not_invent_normal(tmp_path):
    from dataclasses import replace
    from app.gateways import GatewayUnavailable
    demo, _ = prepare_pipeline_demo(tmp_path)
    adapters, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    class Unknown:
        def assess(self, *args): raise GatewayUnavailable("REPLAY_RECORD_MISSING", "missing")
    adapters = replace(adapters, assessor=Unknown())
    path = damaged_png(demo)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        run = run_sync(demo.repo, adapters, "unknown-assessments")
    finally:
        path.write_bytes(raw)
    assert run.state == "completed_with_errors" and any(e.stage == "classification" and e.code == "REPLAY_RECORD_MISSING" for e in run.errors)
    source = demo.repo.get_source(demo.ticket.source_id)
    result = demo.repo.processing_result(demo.ticket.source_id)
    assert source.needs and all(demo.repo.assessment(n.need_id, result.analysis_digest) is None for n in source.needs)


def test_interrupted_partial_worker_and_repeat_click_reuse_material_success(tmp_path):
    from app.runs import RunService
    from app.schemas import RunRequest
    from dataclasses import replace
    demo, _ = prepare_pipeline_demo(tmp_path)
    adapters, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    entered, release = Event(), Event()
    class Paused:
        def extract_needs(self, doc):
            value = adapters.analysis.extract_needs(doc)
            entered.set()
            assert release.wait(8)
            return value
        def assign_topic(self, *args): return adapters.analysis.assign_topic(*args)
    service = RunService(demo.repo, "replay", replace(adapters, analysis=Paused()))
    path = damaged_png(demo)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        first = service.start(RunRequest())
        assert entered.wait(8)
        assert service.start(RunRequest()).run_id == first.run_id
        cached = {p.name: p.read_bytes() for p in demo.settings.app_media_cache_root.glob("*.json")}
        assert len(cached) == 3
        service.close()
        release.set()
        service.futures[first.run_id].result(timeout=8)
        assert Repository(demo.repo.path).get_run(first.run_id).state == "interrupted"
        again, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
        new = RunService(Repository(demo.repo.path), "replay", again)
        try:
            repeated = new.start(RunRequest())
            new.futures[repeated.run_id].result(timeout=8)
            assert new.repo.get_run(repeated.run_id).state == "completed_with_errors"
            assert new.repo.get_run(repeated.run_id).reused_steps >= 1
            assert {p.name: p.read_bytes() for p in demo.settings.app_media_cache_root.glob("*.json")} == cached
        finally:
            new.close()
    finally:
        release.set()
        service.close()
        path.write_bytes(raw)


def test_assessment_contract_and_evidence_provenance_are_not_inferred(tmp_path):
    from app.assessment import NeedAssessor, assessment_request, checked_assessment
    from app.assessment_schemas import AssessmentResult, NeedAssessment
    from app.gateways import GatewayResult, GatewayUnavailable
    from app.replay import make_record
    from pydantic import ValidationError
    from scripts.partial_pipeline_fixture import AuthoredPartialGateway
    demo, profile = prepare_pipeline_demo(tmp_path)
    adapters, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    source = demo.repo.get_source(demo.ticket.source_id)
    normalized = adapters.normalizer.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=1)
    view = analysis_input(normalized)
    need = adapters.analysis.extract_needs(view.document).needs[0]
    need = need.model_copy(update={"need_id": "synthetic-assessment-need"})
    authored = AuthoredPartialGateway(profile)
    assessment = NeedAssessor(authored, profile, "replay").assess(need, view)
    assert assessment.assessment.priority == "unknown"
    for priority in ("potentially_critical", "unknown"):
        value = assessment.assessment.model_dump()
        value.update(priority=priority, uncertainties=[])
        with pytest.raises(ValidationError): NeedAssessment.model_validate(value)
    for field, value in (("source_id", "foreign"), ("fragment_id", "foreign"), ("quote", "INVENTED_QUOTE")):
        forged = assessment.model_copy(deep=True)
        setattr(forged.assessment.evidence_refs[0], field, value)
        with pytest.raises(GatewayUnavailable): checked_assessment(forged, need, view)
    unsafe = assessment.model_copy(deep=True)
    unsafe.assessment.reason = "Свяжитесь по synthetic@example.invalid"
    with pytest.raises(GatewayUnavailable, match="немаскированные"):
        checked_assessment(unsafe, need, view)
    class Forged:
        def generate(self, request, schema):
            value = authored.generate(request, schema)
            return GatewayResult(value=value.value, metadata=value.metadata.model_copy(update={"request_hash": "0" * 64}))
    with pytest.raises(GatewayUnavailable, match="точному запросу"):
        NeedAssessor(Forged(), profile, "replay").assess(need, view)


def test_weekly_missing_database_never_creates_file(tmp_path):
    from app.weekly import build_weekly_report
    path = tmp_path / "missing.db"
    with pytest.raises(RepositoryError) as exc: build_weekly_report(Repository(path), "replay")
    assert exc.value.code == "REPORT_DATABASE_MISSING" and not path.exists()


def test_revision_publication_rejects_wrong_registered_kind_without_writes(tmp_path):
    from app.processing_schemas import MaterialOutcome, make_normalization_result
    from app.schemas import MaterialError
    demo, _ = prepare_pipeline_demo(tmp_path)
    adapters, _ = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    source = demo.repo.get_source(demo.ticket.source_id)
    result = adapters.normalizer.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=1)
    image = next(m for m in result.materials if m.kind == "image")
    wrong = MaterialOutcome(kind="audio", asset_id=image.asset_id, input_hash=image.input_hash, state="failed",
        error=MaterialError(source_id=source.ticket.source_id, asset_id=image.asset_id, stage="asr", code="SYNTHETIC_FAILURE", message="Синтетическая ошибка"))
    forged = make_normalization_result(result.context, result.profile_hash, result.document.normalization_version,
        [wrong if m.asset_id == image.asset_id else m for m in result.materials])
    before = demo.repo.path.read_bytes()
    with pytest.raises(RepositoryError) as exc: demo.repo.publish_normalization(forged)
    assert exc.value.code == "MATERIAL_KIND_MISMATCH" and demo.repo.path.read_bytes() == before
