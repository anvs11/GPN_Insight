"""Одна локальная задача календарной недели; без рассылки и модельных вызовов."""

from datetime import timedelta
import logging
import sqlite3
from threading import Event, Thread

from fastapi import APIRouter, Request

from .api import _filters
from .clock import now, fixed_clock
from .repositories import RepositoryError
from .report_archive import read_snapshot, save_snapshot
from .weekly import build_weekly_report, completed_week
from .workspace_schemas import WeeklySchedule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/reports")


class WeeklyArchive:
    def __init__(self, repository, mode, enabled=True):
        self.repository, self.mode, self.enabled = repository, mode, enabled
        self._stop = Event()
        self._thread = None
        self._saved_period = None
        self.last_error = None

    def tick(self, at=None):
        if not self.enabled:
            return None
        at = at or now()
        week = completed_week(at)
        key = week.end_utc_exclusive.isoformat()
        if self._saved_period == key:
            return None
        # BEGIN IMMEDIATE: проверка наличия и новая запись неделимы даже
        # при одновременном ручном сохранении/запуске второго процесса.
        with fixed_clock(at), self.repository.transaction() as repo:
            for row in repo._db.execute("SELECT body_json FROM report_snapshots"):
                snapshot = read_snapshot(row[0])
                if snapshot["kind"] == "weekly" and snapshot["period"] == week.model_dump(mode="json") and snapshot["model_mode"] == self.mode:
                    self._saved_period = key
                    self.last_error = None
                    return snapshot
            period = week.model_dump(mode="json")
            result = save_snapshot(repo._db, dict(kind="weekly",
                title=f"Неделя {week.period.date_from} — {week.period.date_to}",
                summary="", comment="", selections=[], cards=[], selection_count=0, period=period,
                model_mode=self.mode, markdown=build_weekly_report(repo, self.mode),
                limitations="Автоматический снимок при работающем сервере. При выключенном сервере — последняя завершённая неделя после запуска. Replay/authored не измеряет качество живых моделей."))
        # Отметка только после успешного COMMIT: при сбое можно повторить.
        self._saved_period = key
        self.last_error = None
        return result

    def poll(self):
        try:
            return self.tick()
        except (RepositoryError, sqlite3.Error, OSError, ValueError):
            self.last_error = "WEEKLY_ARCHIVE_FAILED"
            logger.warning("WEEKLY_ARCHIVE_FAILED")
            return None

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        def work():
            while not self._stop.is_set():
                self.poll()
                self._stop.wait(30)
        self._thread = Thread(target=work, name="weekly-archive", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join()

    def status(self):
        week = completed_week()
        return dict(enabled=self.enabled, timezone="Europe/Moscow",
            next_run_at=(week.end_utc_exclusive + timedelta(days=7)).isoformat(),
            last_error=self.last_error, catch_up="latest_completed_week")


@router.get("/weekly-schedule", response_model=WeeklySchedule)
def schedule_status(request: Request):
    _filters(request, set())
    return request.app.state.weekly_archive.status()
