"""SQLite: короткие транзакции, повторная инициализация и миграции без сброса."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .migrations import execute_statements, migrate_v1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    ticket_json TEXT NOT NULL,
    document_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS needs (
    need_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    body_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY,
    body_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_needs (
    topic_id TEXT NOT NULL REFERENCES topics(topic_id),
    need_id TEXT NOT NULL REFERENCES needs(need_id),
    PRIMARY KEY (topic_id, need_id)
);
CREATE TABLE IF NOT EXISTS decision_events (
    event_id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(topic_id),
    topic_version INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    UNIQUE (topic_id, topic_version)
);
CREATE TABLE IF NOT EXISTS demo_fixtures (
    fixture_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL
);
"""


@contextmanager
def connection(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


@dataclass(frozen=True)
class InitializationResult:
    version: int
    migrated_from: int | None = None
    backup_path: Path | None = None


def _version(db: sqlite3.Connection) -> int | None:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
    if not exists:
        return None
    versions = [r[0] for r in db.execute("SELECT version FROM schema_version")]
    if len(versions) != 1 or versions[0] not in {1, 2}:
        raise RuntimeError("Версия базы не поддерживается; исходная база не менялась")
    return versions[0]


def initialize(path: Path) -> InitializationResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    with connection(path) as db:
        previous = _version(db)
        if previous == 1:
            folder = path.parent / "backups"
            folder.mkdir(exist_ok=True)
            backup_path = folder / f"{path.stem}.v1.{uuid4().hex}.db"
            with sqlite3.connect(backup_path) as backup:
                db.backup(backup)
        db.execute("BEGIN IMMEDIATE")
        current = _version(db)
        if current is None:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                raise RuntimeError("База без версии содержит неизвестные таблицы; инициализация отменена")
            execute_statements(db, SCHEMA)
            current = 1
        if current == 1:
            migrate_v1(db)
    return InitializationResult(2, previous if previous == 1 else None, backup_path)
