from datetime import datetime
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image, PdfParser
import pytest

from app.clock import fixed_clock
from app.config import Settings
from app.db import initialize
from app.main import create_app
from app.repositories import Repository, RepositoryError
from app.report_pdf import render_pdf
from app.weekly_archive import WeeklyArchive


def at(value):
    return datetime.fromisoformat(value)


def test_schedule_calendar_boundary_restart_and_no_duplicates(tmp_path):
    path = tmp_path / "schedule.db"
    initialize(path)
    repo = Repository(path)
    task = WeeklyArchive(repo, "replay")
    sunday = task.tick(at("2026-11-08T23:59:59+03:00"))
    assert sunday["period"]["period"] == {"product_id": "webneft", "date_from": "2026-10-26", "date_to": "2026-11-01"}
    monday = task.tick(at("2026-11-09T00:00:00+03:00"))
    assert monday["period"]["period"]["date_from"] == "2026-11-02"
    assert monday["period"]["period"]["date_to"] == "2026-11-08"
    assert monday["period"]["end_utc_exclusive"] == "2026-11-08T21:00:00Z"
    assert task.tick(at("2026-11-09T00:00:30+03:00")) is None
    reopened = WeeklyArchive(Repository(path), "replay").tick(at("2026-11-10T12:00:00+03:00"))
    assert reopened == monday
    with repo._session() as db:
        assert db.execute("SELECT count(*) FROM report_snapshots").fetchone()[0] == 2


def test_schedule_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    import app.weekly_archive as module
    path = tmp_path / "schedule.db"
    initialize(path)
    task = WeeklyArchive(Repository(path), "replay")
    original = module.save_snapshot
    def fail(db, payload):
        original(db, payload)
        raise RepositoryError("SYNTHETIC_FAILURE", "Synthetic test failure", 503)
    monkeypatch.setattr(module, "save_snapshot", fail)
    with fixed_clock(at("2026-11-09T00:00:00+03:00")):
        assert task.poll() is None
        assert task.status()["last_error"] == "WEEKLY_ARCHIVE_FAILED"
        with task.repository._session() as db:
            assert db.execute("SELECT count(*) FROM report_snapshots").fetchone()[0] == 0
        monkeypatch.setattr(module, "save_snapshot", original)
        assert task.poll()["kind"] == "weekly"
        assert task.last_error is None


def test_schedule_starts_with_app_and_stops_without_sending(tmp_path):
    settings = Settings(_env_file=None, app_db_path=tmp_path / "app.db", app_weekly_archive_enabled=True)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        task = client.app.state.weekly_archive
        # Join only a bounded scheduler tick, no real wait for midnight.
        import time
        end = time.monotonic() + 5
        while task._saved_period is None and time.monotonic() < end:
            time.sleep(.01)
        assert client.get("/api/v1/reports/archive").json()["total"] == 1
        state = client.get("/api/v1/reports/weekly-schedule").json()
        assert state["enabled"] and state["timezone"] == "Europe/Moscow"
        assert state["last_error"] is None
    assert not task._thread.is_alive()


def test_pdf_download_cyrillic_pages_and_immutable_archive(tmp_path):
    settings = Settings(_env_file=None, app_db_path=tmp_path / "pdf.db", app_weekly_archive_enabled=False)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        saved = client.post("/api/v1/reports/archive/weekly").json()
        response = client.get(f"/api/v1/reports/archive/{saved['report_id']}/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["cache-control"] == "no-store"
        assert response.content.startswith(b"%PDF-")
        parser = PdfParser.PdfParser(buf=response.content)
        assert len(parser.pages) >= 2
        assert client.get(f"/api/v1/reports/archive/{saved['report_id']}").json() == saved
        assert client.get("/api/v1/reports/archive/missing/pdf").status_code == 404
        # Разбираем реальную страницу PDF, а не только сигнатуру.
        page = parser.read_indirect(parser.pages[0])
        resources = page[b"Resources"]
        image_ref = next(iter(resources[b"XObject"].values()))
        stream = parser.read_indirect(image_ref)
        with Image.open(BytesIO(stream.buf)) as image:
            assert image.size == (1240, 1754)
        parser.close()


def test_pdf_no_font_and_overflow_explicit_without_truncation(monkeypatch):
    import app.report_pdf as module
    sample = dict(report_id="synthetic", title="Синтетическая кириллица", generated_at="2026-11-09", model_mode="replay", markdown="Проверка", limitations="authored")
    with monkeypatch.context() as scope:
        scope.setattr(module, "FONT_PATHS", ())
        with pytest.raises(RepositoryError) as failure:
            render_pdf(sample)
        assert failure.value.code == "PDF_FONT_UNAVAILABLE"
    with monkeypatch.context() as scope:
        scope.setattr(module, "MAX_PAGES", 1)
        with pytest.raises(RepositoryError) as failure:
            render_pdf({**sample, "markdown": "Синтетическая строка\n" * 80})
        assert failure.value.code == "PDF_TOO_LARGE"
    assert render_pdf(sample).startswith(b"%PDF-")
