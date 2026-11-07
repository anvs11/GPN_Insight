"""Время приложения; фиксированное значение доступно только CLI/test-контексту."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

_fixed: ContextVar[datetime | None] = ContextVar("gpn_scenario_clock", default=None)


def now() -> datetime:
    return _fixed.get() or datetime.now(timezone.utc)


@contextmanager
def fixed_clock(value: datetime):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock must include timezone")
    token = _fixed.set(value.astimezone(timezone.utc))
    try:
        yield
    finally:
        _fixed.reset(token)
