"""Операции хранения без загрузки файлов, моделей и правил группировки."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .db import connection
from .coordination import gate_for
from .errors import RepositoryError
from .run_storage import RunStorage
from .clock import now
from .schemas import (
    DatasetRegistration, ExtractedNeed, Fragment, ImportCounts, ImportLogEntry,
    ImportResult, ManualOverride, NormalizedDocument, RegisteredMaterial,
    ReviewItem, Run, SourceDetail, TicketInput, Topic,
)


def digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_content_hash(ticket: TicketInput, materials: dict[str, RegisteredMaterial]) -> str:
    """Общий алгоритм идентичности источника для регистрации и preview."""
    value = ticket.model_dump(mode="json")
    value["assets"] = [
        {"asset_id": a.asset_id, "kind": a.kind, "mime_type": a.mime_type,
         "source_url": a.source_url, "content_hash": materials[a.asset_id].content_hash}
        for a in sorted(ticket.assets, key=lambda a: a.asset_id)
    ]
    return digest(value)


class StorageRepository(RunStorage):
    def __init__(self, path: Path, *, _db: sqlite3.Connection | None = None):
        self.path = path
        self._db = _db

    @contextmanager
    def _session(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        if self._db is not None:
            if not write:
                yield self._db
                return
            savepoint = "op_" + uuid4().hex
            self._db.execute(f"SAVEPOINT {savepoint}")
            try:
                yield self._db
            except BaseException:
                self._db.execute(f"ROLLBACK TO {savepoint}")
                raise
            finally:
                self._db.execute(f"RELEASE {savepoint}")
            return
        with connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db

    @contextmanager
    def transaction(self):
        """Объединяет несколько repository-операций; commit только на выходе."""
        with self._session(write=True) as db:
            yield type(self)(self.path, _db=db)

    @staticmethod
    def _source(db, product_id, source_id, version=None):
        row = db.execute(
            "SELECT r.*,v.version,v.content_hash,v.ticket_json,v.needs_extracted FROM source_records r "
            "JOIN source_versions v ON v.product_id=r.product_id AND v.source_id=r.source_id "
            "WHERE r.product_id=? AND r.source_id=? AND v.version=coalesce(?,r.current_version)",
            (product_id, source_id, version),
        ).fetchone()
        if row is None:
            raise RepositoryError("SOURCE_NOT_FOUND", "Источник или его версия не найдены.", 404)
        return row

    @staticmethod
    def _validate_evidence(db, product_id, refs):
        for ref in refs:
            row = db.execute("SELECT body_json FROM normalized_fragments WHERE fragment_id=? AND product_id=? AND source_id=?", (ref.fragment_id, product_id, ref.source_id)).fetchone()
            if row is None or ref.quote not in Fragment.model_validate_json(row[0]).text:
                raise RepositoryError("INVALID_EVIDENCE", "Цитата не подтверждена фрагментом нужного продукта и источника.", 422)

    def get_source(self, source_id: str, product_id: str = "webneft", version: int | None = None) -> SourceDetail:
        with self._session() as db:
            row = self._source(db, product_id, source_id, version)
            key = (product_id, source_id, row["version"])
            doc = db.execute("SELECT body_json FROM normalized_documents WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()
            needs = db.execute("SELECT body_json FROM stored_needs WHERE product_id=? AND source_id=? AND source_version=? ORDER BY need_id", key).fetchall()
        normalized = NormalizedDocument.model_validate_json(doc[0]) if doc else None
        return SourceDetail(
            ticket=TicketInput.model_validate_json(row["ticket_json"]),
            document=normalized,
            needs=[ExtractedNeed.model_validate_json(n[0]) for n in needs],
            source_version=row["version"], content_hash=row["content_hash"],
            requires_analysis=not bool(row["needs_extracted"]) or bool(normalized and normalized.errors),
            synthetic=bool(row["synthetic"]), data_origin=row["origin"],
        )

    def get_source_history(self, source_id: str, product_id: str = "webneft") -> list[SourceDetail]:
        with self.transaction() as tx:
            versions = tx._db.execute("SELECT version FROM source_versions WHERE product_id=? AND source_id=? ORDER BY version", (product_id, source_id)).fetchall()
            return [tx.get_source(source_id, product_id, r[0]) for r in versions]

    def preview_dataset_counts(self, batch: DatasetRegistration) -> ImportCounts:
        """Только снимок чтения существующей SQLite v2, без создания/миграции."""
        batch = DatasetRegistration.model_validate(batch.model_dump())
        materials = {m.asset_id: m for m in batch.materials}
        counts = ImportCounts()
        try:
            if not self.path.is_file():
                raise RepositoryError(
                    "PREVIEW_DATABASE_MISSING",
                    "База для проверки отсутствует. Подготовьте её отдельно по README и повторите проверку.",
                    503,
                )
            # Даже mode=ro может создать -wal/-shm. Не открываем WAL-базу через SQLite.
            with self.path.open("rb") as source:
                header = source.read(20)
            if header[:16] == b"SQLite format 3\x00" and header[18:20] != b"\x01\x01":
                raise RepositoryError(
                    "PREVIEW_JOURNAL_UNSUPPORTED",
                    "Режим журнала базы пока не поддержан preview без записи. Используйте отдельную SQLite v2 с обычным журналом.",
                    503,
                )
            # mode=ro защищает также от создания файла при гонке после is_file().
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only = ON")
                db.execute("BEGIN")
                if [r[0] for r in db.execute("SELECT version FROM schema_version")] not in ([2], [3]):
                    raise RepositoryError(
                        "PREVIEW_SCHEMA_UNSUPPORTED",
                        "Для проверки нужна SQLite v2/v3. Обновите базу отдельно по README.",
                        503,
                    )
                source_query = (
                    "SELECT v.content_hash,r.synthetic,r.origin FROM source_records r "
                    "LEFT JOIN source_versions v ON v.product_id=r.product_id AND v.source_id=r.source_id "
                    "AND v.version=r.current_version"
                )
                # Проверяем нужные таблицы/поля и для пустого tickets, без чтения обращений.
                db.execute(source_query + " LIMIT 0")
                for ticket in batch.tickets:
                    current = db.execute(
                        source_query + " WHERE r.product_id=? AND r.source_id=?",
                        (batch.product_id, ticket.source_id),
                    ).fetchone()
                    if current is None:
                        counts.inserted += 1
                    elif current["content_hash"] is None:
                        raise sqlite3.DatabaseError("Missing current source version")
                    elif (bool(current["synthetic"]), current["origin"]) != (batch.synthetic, batch.origin):
                        raise RepositoryError(
                            "SOURCE_PROVENANCE_CONFLICT",
                            "Происхождение существующего источника нельзя незаметно изменить.",
                        )
                    elif current["content_hash"] == source_content_hash(ticket, materials):
                        counts.unchanged += 1
                    else:
                        counts.updated += 1
        except (sqlite3.Error, OSError) as exc:
            raise RepositoryError(
                "PREVIEW_DATABASE_UNAVAILABLE",
                "Базу не удалось прочитать. Проверьте её состояние и доступ; preview ничего не сохраняет.",
                503,
            ) from exc
        return counts

    def register_dataset(self, batch: DatasetRegistration, *, registration_id: str | None = None) -> ImportResult:
        with self.import_commit():
            return self._register_dataset(batch, registration_id=registration_id)

    @contextmanager
    def import_commit(self):
        """Будущий HTTP-импорт удерживает guard также во время размещения файлов."""
        with gate_for(self.path).importing(wait=self._db is None):
            if self.active_run() is not None:
                raise RepositoryError("RUN_ACTIVE", "Дождитесь завершения активного запуска перед импортом.")
            yield

    def _register_dataset(self, batch: DatasetRegistration, *, registration_id: str | None = None) -> ImportResult:
        """Только SQLite. Файлы уже проверены/перемещены участником 1 до вызова."""
        batch = DatasetRegistration.model_validate(batch.model_dump())
        request_hash = digest(batch.model_dump(mode="json"))
        registration_id = registration_id or str(uuid4())
        with self._session(write=True) as db:
            previous = db.execute("SELECT * FROM import_log WHERE registration_id=?", (registration_id,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise RepositoryError("REGISTRATION_CONFLICT", "Этот ID регистрации уже использован для другого запроса.")
                return ImportLogEntry.model_validate_json(previous["body_json"]).result
            if db.execute("SELECT 1 FROM run_records WHERE state IN ('queued','running')").fetchone():
                raise RepositoryError("RUN_ACTIVE", "Регистрация недоступна во время активного запуска.")
            materials = {m.asset_id: m for m in batch.materials}
            counts = ImportCounts()
            for ticket in batch.tickets:
                content_hash = source_content_hash(ticket, materials)
                key = (batch.product_id, ticket.source_id)
                current = db.execute("SELECT r.current_version,v.content_hash,r.synthetic,r.origin FROM source_records r JOIN source_versions v "
                                     "ON v.product_id=r.product_id AND v.source_id=r.source_id AND v.version=r.current_version "
                                     "WHERE r.product_id=? AND r.source_id=?", key).fetchone()
                if current and (bool(current["synthetic"]), current["origin"]) != (batch.synthetic, batch.origin):
                    raise RepositoryError("SOURCE_PROVENANCE_CONFLICT", "Происхождение существующего источника нельзя незаметно изменить.")
                if current and current["content_hash"] == content_hash:
                    counts.unchanged += 1
                    continue
                version = current["current_version"] + 1 if current else 1
                if current:
                    counts.updated += 1
                    affected = db.execute("SELECT DISTINCT t.topic_id,t.body_json FROM topics t JOIN topic_need_links l ON t.topic_id=l.topic_id "
                                          "JOIN stored_needs n ON l.need_id=n.need_id WHERE n.product_id=? AND n.source_id=? AND n.active=1 AND l.active=1", key).fetchall()
                    db.execute("UPDATE source_records SET current_version=? WHERE product_id=? AND source_id=?", (version, *key))
                    db.execute("UPDATE stored_needs SET active=0 WHERE product_id=? AND source_id=?", key)
                    for changed_topic in affected:
                        topic = Topic.model_validate_json(changed_topic["body_json"])
                        db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (topic.model_copy(update={"version": topic.version + 1}).model_dump_json(), topic.topic_id))
                    db.execute("UPDATE manual_overrides SET requires_recheck=1 WHERE product_id=? AND source_id=?", key)
                    db.execute("UPDATE review_records SET requires_recheck=1 WHERE review_id IN "
                               "(SELECT rn.review_id FROM review_needs rn JOIN stored_needs n ON n.need_id=rn.need_id WHERE n.product_id=? AND n.source_id=?)", key)
                else:
                    counts.inserted += 1
                    db.execute("INSERT INTO source_records VALUES (?,?,?,?,?)", (*key, version, int(batch.synthetic), batch.origin))
                db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,0)", (*key, version, content_hash, ticket.model_dump_json(), now().isoformat()))
                for asset in ticket.assets:
                    material = materials[asset.asset_id]
                    existing = db.execute("SELECT body_json FROM registered_materials WHERE product_id=? AND asset_id=? AND content_hash=?", (batch.product_id, asset.asset_id, material.content_hash)).fetchone()
                    if existing:
                        registered = RegisteredMaterial.model_validate_json(existing[0])
                        if (registered.kind, registered.mime_type, registered.byte_size) != (material.kind, material.mime_type, material.byte_size):
                            raise RepositoryError("MATERIAL_CONFLICT", "Метаданные совпадающего материала противоречат сохранённым.")
                    else:
                        db.execute("INSERT INTO registered_materials VALUES (?,?,?,?,?)", (batch.product_id, asset.asset_id, material.content_hash, material.storage_path, material.model_dump_json()))
                    db.execute("INSERT INTO source_materials VALUES (?,?,?,?,?)", (*key, version, asset.asset_id, material.content_hash))
            result = ImportResult(dataset_id=batch.dataset_id, counts=counts, imported_at=now())
            entry = ImportLogEntry(registration_id=registration_id, product_id=batch.product_id, result=result, synthetic=batch.synthetic)
            db.execute("INSERT INTO import_log VALUES (?,?,?,?)", (registration_id, batch.product_id, request_hash, entry.model_dump_json()))
            return result

    def list_imports(self, product_id: str = "webneft") -> list[ImportLogEntry]:
        with self._session() as db:
            return [ImportLogEntry.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM import_log WHERE product_id=? ORDER BY rowid", (product_id,))]

    def registered_materials(self, product_id: str | None = None) -> list[RegisteredMaterial]:
        with self._session() as db:
            return [RegisteredMaterial.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM registered_materials WHERE (? IS NULL OR product_id=?) ORDER BY storage_path", (product_id, product_id))]

    def source_materials(self, source_id: str, product_id: str = "webneft", version: int | None = None) -> list[RegisteredMaterial]:
        with self._session() as db:
            row = self._source(db, product_id, source_id, version)
            rows = db.execute("SELECT m.body_json FROM registered_materials m JOIN source_materials s ON m.product_id=s.product_id AND m.asset_id=s.asset_id AND m.content_hash=s.content_hash "
                              "WHERE s.product_id=? AND s.source_id=? AND s.source_version=? ORDER BY m.asset_id", (product_id, source_id, row["version"]))
            return [RegisteredMaterial.model_validate_json(r[0]) for r in rows]

    def save_normalized_document(self, document: NormalizedDocument) -> None:
        document = NormalizedDocument.model_validate(document.model_dump())
        with self._session(write=True) as db:
            row = self._source(db, document.product_id, document.source_id, document.version)
            if row["current_version"] != document.version or row["content_hash"] != document.content_hash:
                raise RepositoryError("SOURCE_VERSION_CONFLICT", "Источник изменился; перечитайте текущую версию перед сохранением.")
            key = (document.product_id, document.source_id, document.version)
            existing = db.execute("SELECT body_json FROM normalized_documents WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()
            if existing:
                if NormalizedDocument.model_validate_json(existing[0]) == document:
                    return
                if db.execute("SELECT 1 FROM stored_needs WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone():
                    raise RepositoryError("NORMALIZATION_CONFLICT", "Нормализация уже используется потребностями; автоматическая перезапись запрещена.")
            assets = {r["asset_id"]: RegisteredMaterial.model_validate_json(r["body_json"]) for r in db.execute(
                "SELECT m.asset_id,m.body_json FROM registered_materials m JOIN source_materials s "
                "ON m.product_id=s.product_id AND m.asset_id=s.asset_id AND m.content_hash=s.content_hash "
                "WHERE s.product_id=? AND s.source_id=? AND s.source_version=?", key)}
            if any(f.asset_id is not None and f.asset_id not in assets for f in document.fragments) or any(e.asset_id is not None and e.asset_id not in assets for e in document.errors):
                raise RepositoryError("ASSET_NOT_REGISTERED", "Фрагмент ссылается на незарегистрированный материал.", 422)
            if any(f.asset_id is not None and assets[f.asset_id].kind != {"asr": "audio", "vision": "image"}[f.kind] for f in document.fragments):
                raise RepositoryError("MATERIAL_KIND_MISMATCH", "ASR-фрагмент требует аудио, а VL-фрагмент — изображение.", 422)
            revision = db.execute("SELECT coalesce(max(revision),0)+1 FROM normalization_history WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()[0]
            db.execute("INSERT INTO normalization_history VALUES (?,?,?,?,?)", (*key, revision, document.model_dump_json()))
            db.execute("INSERT INTO normalized_documents VALUES (?,?,?,?) ON CONFLICT(product_id,source_id,source_version) DO UPDATE SET body_json=excluded.body_json", (*key, document.model_dump_json()))
            if existing:
                db.execute("DELETE FROM normalized_fragments WHERE product_id=? AND source_id=? AND source_version=?", key)
                db.execute("UPDATE source_versions SET needs_extracted=0 WHERE product_id=? AND source_id=? AND version=?", key)
            for fragment in document.fragments:
                db.execute("INSERT INTO normalized_fragments VALUES (?,?,?,?,?)", (fragment.fragment_id, *key, fragment.model_dump_json()))

    def normalization_history(self, source_id: str, product_id: str = "webneft", version: int | None = None) -> list[NormalizedDocument]:
        with self._session() as db:
            source = self._source(db, product_id, source_id, version)
            return [NormalizedDocument.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM normalization_history WHERE product_id=? AND source_id=? AND source_version=? ORDER BY revision", (product_id, source_id, source["version"]))]

    def save_needs(self, product_id: str, source_id: str, version: int, needs: list[ExtractedNeed]) -> None:
        if type(version) is not int or version < 1:
            raise RepositoryError("INVALID_VERSION", "Нужна положительная целочисленная версия источника.", 422)
        needs = [ExtractedNeed.model_validate(n.model_dump()) for n in needs]
        with self._session(write=True) as db:
            source = self._source(db, product_id, source_id, version)
            if source["current_version"] != version:
                raise RepositoryError("SOURCE_VERSION_CONFLICT", "Потребности относятся к устаревшему источнику.")
            key = (product_id, source_id, version)
            document = db.execute("SELECT body_json FROM normalized_documents WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()
            if document is None:
                raise RepositoryError("NORMALIZATION_REQUIRED", "Сначала сохраните нормализованный документ.", 422)
            fragments = {f.fragment_id: f for f in NormalizedDocument.model_validate_json(document[0]).fragments}
            if len({n.need_id for n in needs}) != len(needs):
                raise RepositoryError("DUPLICATE_NEED", "ID потребностей должны быть уникальны.", 422)
            for need in needs:
                fragment = fragments.get(need.fragment_id)
                if need.source_id != source_id or fragment is None or need.evidence_quote not in fragment.text:
                    raise RepositoryError("INVALID_EVIDENCE", "Потребность не подтверждена фрагментом этого источника.", 422)
            existing = [ExtractedNeed.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM stored_needs WHERE product_id=? AND source_id=? AND source_version=? ORDER BY need_id", key)]
            if source["needs_extracted"]:
                if existing != sorted(needs, key=lambda n: n.need_id):
                    raise RepositoryError("ANALYSIS_CONFLICT", "Извлечение для этой версии уже сохранено; автоматическая перезапись запрещена.")
                return
            for need in needs:
                db.execute("INSERT INTO stored_needs VALUES (?,?,?,?,?,1,?)", (need.need_id, *key, need.fragment_id, need.model_dump_json()))
            db.execute("UPDATE source_versions SET needs_extracted=1 WHERE product_id=? AND source_id=? AND version=?", key)

    def save_source_analysis(self, document: NormalizedDocument, needs: list[ExtractedNeed]) -> None:
        with self.transaction() as tx:
            tx.save_normalized_document(document)
            tx.save_needs(document.product_id, document.source_id, document.version, needs)

    def save_review(self, review: ReviewItem, *, expected_version: int | None = None) -> None:
        """Сохраняет состояние; политику attach/create определяет участник 2."""
        review = ReviewItem.model_validate(review.model_dump())
        with self._session(write=True) as db:
            current = db.execute("SELECT * FROM review_records WHERE review_id=?", (review.review_id,)).fetchone()
            if current:
                if not current["requires_recheck"] and ReviewItem.model_validate_json(current["body_json"]) == review:
                    return
                if type(expected_version) is not int or expected_version != current["version"] or review.version != expected_version + 1 or current["requires_recheck"]:
                    raise RepositoryError("VERSION_CONFLICT", "Review изменён или требует проверки после обновления источника.")
                old = ReviewItem.model_validate_json(current["body_json"])
                if (review.kind, review.need_ids, review.topic_ids) != (old.kind, old.need_ids, old.topic_ids):
                    raise RepositoryError("REVIEW_IDENTITY_CONFLICT", "Нельзя переназначать существующий review другому содержимому.")
            elif expected_version is not None or review.version != 1 or review.status != "open":
                raise RepositoryError("INVALID_REVIEW_VERSION", "Новый review должен быть открытым с версией 1.", 422)
            products = set()
            for need_id in review.need_ids:
                need = db.execute("SELECT * FROM stored_needs WHERE need_id=? AND active=1", (need_id,)).fetchone()
                if need is None:
                    raise RepositoryError("NEED_NOT_ACTIVE", "Review ссылается на отсутствующую или неактивную потребность.", 422)
                products.add(need["product_id"])
            for topic_id in review.topic_ids + review.candidate_topic_ids + ([review.resolution.topic_id] if review.resolution and review.resolution.topic_id else []):
                topic = db.execute("SELECT product_id FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
                if topic is None:
                    raise RepositoryError("TOPIC_NOT_FOUND", "Тема review не найдена.", 404)
                products.add(topic[0])
            if len(products) > 1:
                raise RepositoryError("PRODUCT_MISMATCH", "Review не может объединять разные продукты.", 422)
            if review.requires_recheck:
                raise RepositoryError("REVIEW_RECHECK", "Нужен новый review для текущей версии источника.")
            self._validate_evidence(db, next(iter(products)), review.evidence_refs)
            db.execute("INSERT INTO review_records VALUES (?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET version=excluded.version,status=excluded.status,body_json=excluded.body_json",
                       (review.review_id, review.version, review.status, int(review.requires_recheck), review.model_dump_json()))
            for need_id in review.need_ids:
                db.execute("INSERT OR IGNORE INTO review_needs VALUES (?,?)", (review.review_id, need_id))
            db.execute("INSERT INTO review_history VALUES (?,?,?)", (review.review_id, review.version, review.model_dump_json()))

    def review_history(self, review_id: str) -> list[ReviewItem]:
        with self._session() as db:
            return [ReviewItem.model_validate_json(r[0]) for r in db.execute("SELECT body_json FROM review_history WHERE review_id=? ORDER BY version", (review_id,))]

    def get_review(self, review_id: str) -> ReviewItem:
        with self._session() as db:
            row = db.execute("SELECT * FROM review_records WHERE review_id=?", (review_id,)).fetchone()
            if row is None:
                raise RepositoryError("REVIEW_NOT_FOUND", "Review не найден.", 404)
            return ReviewItem.model_validate_json(row["body_json"]).model_copy(update={"requires_recheck": bool(row["requires_recheck"])})

    def list_reviews(self, status: str | None = "open") -> list[ReviewItem]:
        with self.transaction() as tx:
            return [tx.get_review(r[0]) for r in tx._db.execute("SELECT review_id FROM review_records WHERE (? IS NULL OR status=?) ORDER BY rowid", (status, status)).fetchall()]

    def get_overrides(self, source_id: str, product_id: str = "webneft") -> list[ManualOverride]:
        with self._session() as db:
            return [ManualOverride.model_validate_json(r["body_json"]).model_copy(update={"requires_recheck": bool(r["requires_recheck"])}) for r in db.execute("SELECT * FROM manual_overrides WHERE product_id=? AND source_id=? ORDER BY rowid", (product_id, source_id))]

    def create_run(self, run: Run) -> Run:
        with gate_for(self.path).start_run():
            return self._create_run(run)

    def _create_run(self, run: Run) -> Run:
        run = Run.model_validate(run.model_dump())
        if run.state != "queued" or run.started_at or run.finished_at:
            raise RepositoryError("INVALID_RUN_STATE", "Новый запуск должен иметь состояние queued без начала/окончания.", 422)
        with self._session(write=True) as db:
            active = db.execute("SELECT body_json FROM run_records WHERE state IN ('queued','running')").fetchone()
            if active:
                return Run.model_validate_json(active[0])
            existing = db.execute("SELECT body_json FROM run_records WHERE run_id=?", (run.run_id,)).fetchone()
            if existing:
                raise RepositoryError("RUN_ID_CONFLICT", "Этот ID запуска уже использован.")
            db.execute("INSERT INTO run_records VALUES (?,?,?,?,?)", (run.run_id, run.product_id, run.mode, run.state, run.model_dump_json()))
            return run

    def get_run(self, run_id: str) -> Run:
        with self._session() as db:
            row = db.execute("SELECT body_json FROM run_records WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise RepositoryError("RUN_NOT_FOUND", "Запуск не найден.", 404)
            return Run.model_validate_json(row[0])

    def save_run(self, run: Run) -> None:
        run = Run.model_validate(run.model_dump())
        with self.transaction() as tx:
            previous = tx.get_run(run.run_id)
            if previous == run:
                return
            allowed = {"queued": {"running", "failed", "interrupted"}, "running": {"running", "completed", "completed_with_errors", "failed", "interrupted"}}
            if run.state not in allowed.get(previous.state, set()) or (run.product_id, run.mode, run.created_at) != (previous.product_id, previous.mode, previous.created_at):
                raise RepositoryError("RUN_STATE_CONFLICT", "Недопустимое изменение состояния или идентичности запуска.")
            if run.state in {"completed", "completed_with_errors", "failed", "interrupted"} and run.finished_at is None:
                raise RepositoryError("INVALID_RUN_STATE", "Завершённое состояние требует времени окончания.", 422)
            if run.state == "running" and run.started_at is None:
                raise RepositoryError("INVALID_RUN_STATE", "Выполняющийся запуск требует времени начала.", 422)
            if previous.started_at is not None and run.started_at != previous.started_at:
                raise RepositoryError("RUN_STATE_CONFLICT", "Время начала уже зафиксировано.")
            if run.total_materials < previous.total_materials:
                raise RepositoryError("RUN_PROGRESS_CONFLICT", "Общее число материалов запуска нельзя уменьшить.")
            if run.state in {"completed", "completed_with_errors"}:
                has_errors = bool(run.failed_materials or run.errors)
                if run.successful_materials + run.failed_materials != run.total_materials or (run.state == "completed") == has_errors:
                    raise RepositoryError("INVALID_RUN_STATE", "Состояние завершения не соответствует покрытию или ошибкам.", 422)
            if run.successful_materials < previous.successful_materials or run.failed_materials < previous.failed_materials:
                raise RepositoryError("RUN_PROGRESS_CONFLICT", "Прогресс запуска нельзя уменьшить.")
            if run.completed_steps < previous.completed_steps or run.reused_steps < previous.reused_steps or not set(previous.origins).issubset(run.origins):
                raise RepositoryError("RUN_PROGRESS_CONFLICT", "Контрольные точки и происхождение запуска нельзя уменьшить.")
            tx._db.execute("UPDATE run_records SET state=?,body_json=? WHERE run_id=?", (run.state, run.model_dump_json(), run.run_id))

    def interrupt_active_runs(self) -> int:
        with self.transaction() as tx:
            rows = tx._db.execute("SELECT body_json FROM run_records WHERE state IN ('queued','running')").fetchall()
            for row in rows:
                old = Run.model_validate_json(row[0])
                tx.save_run(Run.model_validate({**old.model_dump(), "state": "interrupted", "finished_at": now()}))
            return len(rows)
