"""Последняя завершённая календарная неделя Москвы, не скользящие семь дней."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime

from .clock import now
from .schemas import Contract, RunRequest


class CompletedWeek(Contract):
    timezone: str = "Europe/Moscow"
    period: RunRequest
    start_utc: AwareDatetime
    end_utc_exclusive: AwareDatetime


def completed_week(at: datetime | None = None) -> CompletedWeek:
    at = at or now()
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Week clock must be timezone-aware")
    zone = ZoneInfo("Europe/Moscow")
    day = at.astimezone(zone).date()
    end = day - timedelta(days=day.weekday())
    start = end - timedelta(days=7)
    return CompletedWeek(period=RunRequest(date_from=start, date_to=end - timedelta(days=1)),
        start_utc=datetime.combine(start, time.min, zone).astimezone(timezone.utc),
        end_utc_exclusive=datetime.combine(end, time.min, zone).astimezone(timezone.utc))


def build_weekly_report(repository, mode):
    from .report import build_report
    from .errors import RepositoryError
    if not repository.path.is_file():
        raise RepositoryError("REPORT_DATABASE_MISSING", "База не найдена. Сначала подготовьте отдельную базу.", 503)
    week = completed_week()
    with repository._session() as db:
        snapshot = type(repository)(repository.path, _db=db)
        current = previous = 0
        previous_start = week.period.date_from - timedelta(days=7)
        for row in db.execute("SELECT source_id FROM source_records WHERE product_id='webneft'").fetchall():
            source = snapshot.get_source(row[0])
            day = source.ticket.created_at.astimezone(ZoneInfo("Europe/Moscow")).date()
            current += week.period.date_from <= day <= week.period.date_to
            previous += previous_start <= day < week.period.date_from
        report = build_report(snapshot, mode, week.period)
    return ("# Завершённая неделя — Europe/Moscow\n\n"
        f"Неделя: {week.period.date_from} — {week.period.date_to}.\n"
        f"UTC: [{week.start_utc.isoformat()}, {week.end_utc_exclusive.isoformat()}).\n"
        "Отбор по created_at обращения; время анализа и решения не заменяет дату обращения.\n"
        "Снимок по запросу или локальному расписанию; автоматическая рассылка не включена.\n\n"
        f"Динамика distinct source_id по created_at: завершённая неделя={current}; предыдущая={previous}; разница={current - previous:+d}. "
        "Это объём зарегистрированных источников, не число инцидентов и не эффект внедрения.\n\n" + report)
