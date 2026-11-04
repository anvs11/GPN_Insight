"""Небольшая схема этапа 1. Импорт и полный pipeline здесь не реализованы."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
        if exists:
            versions = [row[0] for row in db.execute("SELECT version FROM schema_version")]
            if versions != [1]:
                raise RuntimeError("Версия базы не поддерживается; нужна явная миграция")
        db.executescript(SCHEMA)
