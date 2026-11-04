"""Добавочное хранение v2; таблицы основы v1 остаются неприкосновенными."""

import sqlite3
from datetime import datetime, timezone

from .schemas import ExtractedNeed, NormalizedDocument, TicketInput, Topic

SCHEMA_V2 = """
CREATE TABLE source_records (
 product_id TEXT NOT NULL, source_id TEXT NOT NULL, current_version INTEGER NOT NULL CHECK(current_version>0),
 synthetic INTEGER NOT NULL, origin TEXT NOT NULL, PRIMARY KEY(product_id,source_id)
);
CREATE TABLE source_versions (
 product_id TEXT NOT NULL, source_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 content_hash TEXT NOT NULL, ticket_json TEXT NOT NULL, imported_at TEXT NOT NULL,
 needs_extracted INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(product_id,source_id,version),
 FOREIGN KEY(product_id,source_id) REFERENCES source_records(product_id,source_id)
);
CREATE TABLE registered_materials (
 product_id TEXT NOT NULL, asset_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 storage_path TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL,
 PRIMARY KEY(product_id,asset_id,content_hash)
);
CREATE TABLE source_materials (
 product_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 asset_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 PRIMARY KEY(product_id,source_id,source_version,asset_id),
 FOREIGN KEY(product_id,source_id,source_version) REFERENCES source_versions(product_id,source_id,version),
 FOREIGN KEY(product_id,asset_id,content_hash) REFERENCES registered_materials(product_id,asset_id,content_hash)
);
CREATE TABLE normalized_documents (
 product_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(product_id,source_id,source_version),
 FOREIGN KEY(product_id,source_id,source_version) REFERENCES source_versions(product_id,source_id,version)
);
CREATE TABLE normalized_fragments (
 fragment_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, source_id TEXT NOT NULL,
 source_version INTEGER NOT NULL, body_json TEXT NOT NULL,
 UNIQUE(product_id,source_id,source_version,fragment_id),
 FOREIGN KEY(product_id,source_id,source_version) REFERENCES normalized_documents(product_id,source_id,source_version)
);
CREATE TABLE normalization_history (
 product_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 revision INTEGER NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(product_id,source_id,source_version,revision),
 FOREIGN KEY(product_id,source_id,source_version) REFERENCES source_versions(product_id,source_id,version)
);
CREATE TABLE stored_needs (
 need_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 fragment_id TEXT NOT NULL, active INTEGER NOT NULL, body_json TEXT NOT NULL,
 FOREIGN KEY(product_id,source_id,source_version,fragment_id)
 REFERENCES normalized_fragments(product_id,source_id,source_version,fragment_id)
);
CREATE TABLE topic_need_links (
 topic_id TEXT NOT NULL REFERENCES topics(topic_id), need_id TEXT NOT NULL REFERENCES stored_needs(need_id),
 active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(topic_id,need_id)
);
CREATE TABLE catalog_state (product_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE review_records (
 review_id TEXT PRIMARY KEY, version INTEGER NOT NULL, status TEXT NOT NULL,
 requires_recheck INTEGER NOT NULL DEFAULT 0, body_json TEXT NOT NULL
);
CREATE TABLE review_needs (
 review_id TEXT NOT NULL REFERENCES review_records(review_id), need_id TEXT NOT NULL REFERENCES stored_needs(need_id),
 PRIMARY KEY(review_id,need_id)
);
CREATE TABLE review_history (
 review_id TEXT NOT NULL REFERENCES review_records(review_id), version INTEGER NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(review_id,version)
);
CREATE TABLE manual_overrides (
 override_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 need_id TEXT NOT NULL REFERENCES stored_needs(need_id), topic_id TEXT REFERENCES topics(topic_id),
 action TEXT NOT NULL, requires_recheck INTEGER NOT NULL DEFAULT 0, body_json TEXT NOT NULL,
 FOREIGN KEY(product_id,source_id,source_version) REFERENCES source_versions(product_id,source_id,version)
);
CREATE TABLE topic_changes (
 event_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(topic_id), body_json TEXT NOT NULL
);
CREATE TABLE run_records (
 run_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL, body_json TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_run ON run_records((1)) WHERE state IN ('queued','running');
CREATE TABLE import_log (
 registration_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 body_json TEXT NOT NULL
);
CREATE INDEX active_source_needs ON stored_needs(product_id,source_id,source_version,active);
CREATE INDEX topic_active_links ON topic_need_links(topic_id,active);
ALTER TABLE topics ADD COLUMN product_id TEXT NOT NULL DEFAULT 'webneft';
ALTER TABLE topics ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 1;
ALTER TABLE topics ADD COLUMN origin TEXT NOT NULL DEFAULT 'authored';
ALTER TABLE topics ADD COLUMN analysis_mode TEXT;
"""


def execute_statements(db: sqlite3.Connection, script: str) -> None:
    # executescript() делает неявный commit и разрушил бы атомарность миграции.
    for statement in script.split(";"):
        if statement.strip():
            db.execute(statement)


def migrate_v1(db: sqlite3.Connection) -> None:
    execute_statements(db, SCHEMA_V2)
    now = datetime.now(timezone.utc).isoformat()
    products = {}
    for row in db.execute("SELECT * FROM sources").fetchall():
        ticket = TicketInput.model_validate_json(row["ticket_json"])
        document = NormalizedDocument.model_validate_json(row["document_json"])
        if ticket.assets or document.source_id != ticket.source_id or document.product_id != ticket.product_id:
            raise RuntimeError("Не поддерживается автоматический перенос такой v1-записи; исходная база сохранена")
        key = (ticket.product_id, ticket.source_id)
        products[ticket.source_id] = key
        db.execute("INSERT INTO source_records VALUES (?,?,?,1,'authored')", (*key, document.version))
        db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,1)", (*key, document.version, document.content_hash, row["ticket_json"], now))
        db.execute("INSERT INTO normalized_documents VALUES (?,?,?,?)", (*key, document.version, row["document_json"]))
        db.execute("INSERT INTO normalization_history VALUES (?,?,?,1,?)", (*key, document.version, row["document_json"]))
        for fragment in document.fragments:
            db.execute("INSERT INTO normalized_fragments VALUES (?,?,?,?,?)", (fragment.fragment_id, *key, document.version, fragment.model_dump_json()))
    for row in db.execute("SELECT * FROM needs").fetchall():
        need = ExtractedNeed.model_validate_json(row["body_json"])
        product, source = products[need.source_id]
        version = db.execute("SELECT current_version FROM source_records WHERE product_id=? AND source_id=?", (product, source)).fetchone()[0]
        db.execute("INSERT INTO stored_needs VALUES (?,?,?,?,?,1,?)", (need.need_id, product, source, version, need.fragment_id, row["body_json"]))
    for row in db.execute("SELECT * FROM topics").fetchall():
        topic = Topic.model_validate_json(row["body_json"])
        db.execute("UPDATE topics SET product_id=? WHERE topic_id=?", (topic.passport.product_id, topic.topic_id))
        db.execute("INSERT INTO catalog_state VALUES (?,1) ON CONFLICT(product_id) DO UPDATE SET revision=revision+1", (topic.passport.product_id,))
    db.execute("INSERT INTO topic_need_links(topic_id,need_id) SELECT topic_id,need_id FROM topic_needs")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("Перенос нарушил связи; миграция отменена")
    db.execute("UPDATE schema_version SET version=2 WHERE version=1")
