from datetime import date, datetime, timezone
from hashlib import sha256
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from app.config import REPO_ROOT
from app.db import initialize
from app.main import create_app
from app.report import build_report
from app.repositories import Repository, RepositoryError
from app.schemas import DatasetRegistration, DecisionUpdate, RunRequest
from test_human_workflow import two_topics, merge_command
from app.workflow import HumanWorkflow


def test_report_deduplicates_cross_topic_tickets_and_excludes_raw_sources(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    before = sha256(repo.path.read_bytes()).hexdigest()
    report = build_report(repo, "replay", RunRequest())
    assert "Уникальных источников в периоде: 4." in report
    assert "Активных потребностей в периоде: 5" in report
    assert "Уникальных заявителей в периоде: нет данных" in report
    assert "authored" in report and "Синтетические данные" in report and "replay" in report
    assert all(ticket.text not in report for ticket in fixture.sources)
    assert sha256(repo.path.read_bytes()).hexdigest() == before
    HumanWorkflow(repo).merge_topics(source, merge_command(repo, source, target), "replay")
    merged = build_report(repo, "replay", RunRequest())
    assert "Уникальных источников в периоде: 4." in merged and "Активных потребностей в периоде: 5" in merged
    assert "| " + source + " |" not in merged  # исходная merged тема не становится второй активной строкой


def test_report_period_is_moscow_inclusive_and_markdown_is_escaped(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    current = repo.get_topic(source, "replay").topic
    repo.update_passport(source, current.passport.model_copy(update={"version": 2}), expected_version=current.version,
        title='<script>alert(1)</script> | [click](https://evil.example)\n# H', author="Аналитик", reason="Синтетический тест")
    created = fixture.sources[0].created_at.astimezone(ZoneInfo("Europe/Moscow")).date()
    report = build_report(repo, "replay", RunRequest(date_from=created, date_to=created))
    assert "Уникальных источников в периоде: 1." in report
    assert "<script>" not in report and "[click](https://evil.example)" not in report
    assert "&lt;script&gt;" in report and "\\|" in report
    assert "\n# H" not in report


def test_report_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(RepositoryError) as exc:
        build_report(Repository(path), "replay", RunRequest())
    assert exc.value.code == "REPORT_DATABASE_MISSING" and not path.exists()


def test_report_http_uses_real_snapshot_and_rejects_silent_filters(settings, fixture):
    initialize(settings.app_db_path)
    repo = Repository(settings.app_db_path)
    repo.seed_demo(fixture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        result = client.get("/api/v1/report")
        assert result.status_code == 200 and result.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "Уникальных источников в периоде: 4." in result.text
        assert "Уникальных источников в периоде: 4." in client.get("/api/v1/reports").text
        for query in ("mode=live", "date_from=2026-12-01&date_to=2026-10-01", "date_from=bad", "product_id=other", "date_from=2026-10-01&date_from=2026-10-02"):
            assert client.get("/api/v1/report?" + query).status_code == 422


def test_report_cli_saves_utf8_without_initializing_or_changing_database(tmp_path, fixture):
    path = tmp_path / "cli.db"
    initialize(path)
    Repository(path).seed_demo(fixture)
    before = sha256(path.read_bytes()).hexdigest()
    output = tmp_path / "report.md"
    env = dict(os.environ, APP_DB_PATH=str(path), APP_MODEL_MODE="replay", PYTHONUTF8="1")
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", "report", "--date-from", "2026-10-01",
        "--date-to", "2026-10-31", "--output", str(output)], cwd=REPO_ROOT / "backend", env=env,
        capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0 and "Уникальных источников в периоде: 4." in output.read_text(encoding="utf-8")
    assert sha256(path.read_bytes()).hexdigest() == before


def test_late_import_and_implementation_signal_use_created_at(tmp_path, fixture, monkeypatch):
    import app.repositories as repositories
    clock = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(repositories, "now", lambda: clock)
    repo, source, target = two_topics(tmp_path, fixture)
    topic = repo.get_topic(source, "replay").topic
    repo.record_decision(source, DecisionUpdate(version=topic.version, status="implemented", owner="Владелец",
        reason="Внедрено", implemented_at="2026-11-01T09:00:00Z", implementation_comment="Синтетическая реализация"))
    original_history = repo.get_topic(source, "replay").history
    for source_id, created in (("late-old", "2026-10-30T12:00:00Z"), ("after-implementation", "2026-11-02T12:00:00Z"),
                               ("at-implementation", "2026-11-01T09:00:00Z")):
        ticket = fixture.sources[0].model_copy(update={"source_id": source_id, "created_at": datetime.fromisoformat(created)})
        repo.register_dataset(DatasetRegistration(dataset_id="late-test", synthetic=True, tickets=[ticket]))
        detail = repo.get_source(source_id)
        doc = fixture.documents[0].model_copy(update={"source_id": source_id, "content_hash": detail.content_hash,
            "fragments": [fixture.documents[0].fragments[0].model_copy(update={"fragment_id": "fragment-" + source_id})]})
        need = fixture.needs[0].model_copy(update={"source_id": source_id, "fragment_id": doc.fragments[0].fragment_id, "need_id": "need-" + source_id})
        repo.save_source_analysis(doc, [need])
        card = repo.get_topic(source, "replay")
        repo.attach_needs(source, [need.need_id], expected_version=card.topic.version)
    saved = Repository(repo.path).get_topic(source, "replay")
    assert saved.topic.status == "implemented" and saved.history == original_history
    assert saved.stats.post_implementation_ticket_count == 1 and saved.stats.new_ticket_count_since_decision == 0


def test_report_date_boundary_uses_moscow_and_keeps_unprocessed_source_visible(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    ticket = fixture.sources[0].model_copy(update={"source_id": "moscow-midnight",
        "created_at": datetime(2026, 11, 30, 21, 30, tzinfo=timezone.utc)})
    repo.register_dataset(DatasetRegistration(dataset_id="midnight", synthetic=True, tickets=[ticket]))
    report = build_report(repo, "replay", RunRequest(date_from=date(2026, 12, 1), date_to=date(2026, 12, 1)))
    assert "Уникальных источников в периоде: 1." in report
    assert "ожидают анализа/содержат ошибки: 1." in report
    assert "не подтверждает отсутствие потребностей" in report


def test_report_coverage_one_does_not_hide_assignment_error(tmp_path, fixture):
    from app.schemas import Run, MaterialError
    repo, source, target = two_topics(tmp_path, fixture)
    created = datetime(2026, 11, 1, tzinfo=timezone.utc)
    run = Run(run_id="synthetic-error-run", mode="replay", state="queued", stage="extract", created_at=created,
        total_materials=1, successful_materials=0, failed_materials=0, coverage=0)
    repo.create_run(run)
    running = run.model_copy(update={"state": "running", "started_at": created})
    repo.save_run(running)
    repo.save_run(running.model_copy(update={"state": "completed_with_errors", "finished_at": created,
        "successful_materials": 1, "coverage": 1, "origins": ["authored"],
        "errors": [MaterialError(source_id=fixture.sources[0].source_id, stage="assignment", code="SYNTHETIC_ASSIGNMENT_ERROR",
            message="SENSITIVE_TEST_SENTINEL")]}))
    report = build_report(repo, "replay", RunRequest())
    assert "completed\\_with\\_errors" in report and "coverage=1" in report
    assert "SYNTHETIC\\_ASSIGNMENT\\_ERROR" in report and "SENSITIVE_TEST_SENTINEL" not in report


def test_report_cli_cannot_overwrite_database(tmp_path, fixture):
    path = tmp_path / "do-not-overwrite.db"
    initialize(path)
    Repository(path).seed_demo(fixture)
    before = sha256(path.read_bytes()).hexdigest()
    env = dict(os.environ, APP_DB_PATH=str(path), APP_MODEL_MODE="replay", PYTHONUTF8="1")
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", "report", "--output", str(path)],
        cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0 and "REPORT_OUTPUT_EXISTS" in result.stderr and sha256(path.read_bytes()).hexdigest() == before
