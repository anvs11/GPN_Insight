"""История partial результатов и атомарная активация анализа без удаления evidence."""

from .errors import RepositoryError
from .processing_schemas import NormalizationResult, analysis_input
from .schemas import ExtractedNeed, Fragment, NormalizedDocument, RegisteredMaterial


class ProcessingStorage:
    def has_active_assignment(self, need_id):
        with self._session() as db:
            return bool(db.execute("SELECT 1 FROM topic_need_links l JOIN stored_needs n ON n.need_id=l.need_id "
                "WHERE l.need_id=? AND l.active=1 AND n.active=1 AND NOT EXISTS "
                "(SELECT 1 FROM manual_overrides o WHERE o.need_id=n.need_id AND o.requires_recheck=1)", (need_id,)).fetchone())

    def assessment(self, need_id, analysis_digest):
        from .assessment_schemas import AssessmentResult
        with self._session() as db:
            row = db.execute("SELECT a.body_json FROM need_assessments a JOIN processing_results r ON r.revision_id=a.revision_id "
                             "WHERE a.need_id=? AND r.analysis_digest=? ORDER BY a.rowid DESC LIMIT 1", (need_id, analysis_digest)).fetchone()
            return AssessmentResult.model_validate_json(row[0]) if row else None

    def save_assessment(self, result, normalization):
        from .assessment import checked_assessment
        with self._session(write=True) as db:
            row = db.execute("SELECT body_json FROM stored_needs WHERE need_id=? AND active=1", (result.need_id,)).fetchone()
            if not row or not db.execute("SELECT 1 FROM processing_needs WHERE revision_id=? AND need_id=?", (normalization.revision_id, result.need_id)).fetchone():
                raise RepositoryError("ASSESSMENT_NEED_INVALID", "Оценка требует активной потребности указанной ревизии.", 422)
            result = checked_assessment(result, ExtractedNeed.model_validate_json(row[0]), analysis_input(normalization))
            old = db.execute("SELECT body_json FROM need_assessments WHERE need_id=? AND revision_id=?", (result.need_id, normalization.revision_id)).fetchone()
            if old:
                from .assessment_schemas import AssessmentResult
                if AssessmentResult.model_validate_json(old[0]) != result:
                    raise RepositoryError("ASSESSMENT_CONFLICT", "Сохранённую оценку нельзя перезаписать без новой версии.")
                return
            db.execute("INSERT INTO need_assessments VALUES (?,?,?)", (result.need_id, normalization.revision_id, result.model_dump_json()))

    def _has_processing(self):
        with self._session() as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='processing_heads'").fetchone())

    def processing_result(self, source_id, product_id="webneft", version=None, revision_id=None):
        with self._session() as db:
            source = self._source(db, product_id, source_id, version)
            key = (product_id, source_id, source["version"])
            if revision_id is None:
                row = db.execute("SELECT r.body_json FROM processing_heads h JOIN processing_results r ON r.revision_id=h.revision_id "
                                 "WHERE h.product_id=? AND h.source_id=? AND h.source_version=?", key).fetchone()
            else:
                row = db.execute("SELECT body_json FROM processing_results WHERE product_id=? AND source_id=? AND source_version=? AND revision_id=?",
                                 (*key, revision_id)).fetchone()
            return NormalizationResult.model_validate_json(row[0]) if row else None

    def processing_pending(self, source_id, product_id="webneft", version=None):
        with self._session() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='processing_heads'").fetchone():
                return False
            source = self._source(db, product_id, source_id, version)
            row = db.execute("SELECT r.analysis_digest,a.analysis_digest FROM processing_heads h "
                             "JOIN processing_results r ON r.revision_id=h.revision_id "
                             "LEFT JOIN processing_results a ON a.revision_id=h.analyzed_revision "
                             "WHERE h.product_id=? AND h.source_id=? AND h.source_version=?",
                             (product_id, source_id, source["version"])).fetchone()
            return bool(row and row[0] != row[1])

    def publish_normalization(self, result: NormalizationResult):
        result = NormalizationResult.model_validate(result.model_dump())
        c, document = result.context, result.document
        key = (c.product_id, c.source_id, c.source_version)
        with self._session(write=True) as db:
            source = self._source(db, *key)
            if (source["current_version"] != c.source_version or source["content_hash"] != c.content_hash
                    or bool(source["synthetic"]) != c.synthetic or source["origin"] != c.data_origin):
                raise RepositoryError("SOURCE_VERSION_CONFLICT", "Контекст результата изменился; повторите обработку.")
            registered = {r[0] for r in db.execute("SELECT asset_id FROM source_materials WHERE product_id=? AND source_id=? AND source_version=?", key)}
            if registered != {m.asset_id for m in result.materials if m.asset_id is not None}:
                raise RepositoryError("ASSET_NOT_REGISTERED", "Результат не покрывает точный набор материалов.", 422)
            hashes = {r[0]: r[1] for r in db.execute("SELECT asset_id,content_hash FROM source_materials WHERE product_id=? AND source_id=? AND source_version=?", key)}
            if any(m.asset_id is not None and hashes[m.asset_id] != m.input_hash for m in result.materials):
                raise RepositoryError("MATERIAL_HASH_CONFLICT", "Исход материала не соответствует зарегистрированным bytes.", 422)
            kinds = {r[0]: RegisteredMaterial.model_validate_json(r[1]).kind for r in db.execute(
                "SELECT s.asset_id,m.body_json FROM source_materials s JOIN registered_materials m "
                "ON m.product_id=s.product_id AND m.asset_id=s.asset_id AND m.content_hash=s.content_hash "
                "WHERE s.product_id=? AND s.source_id=? AND s.source_version=?", key)}
            if any(m.asset_id is not None and kinds.get(m.asset_id) != m.kind for m in result.materials):
                raise RepositoryError("MATERIAL_KIND_MISMATCH", "Исход должен соответствовать типу зарегистрированного материала.", 422)
            # Исторический fragment ID никогда не получает другой текст/контекст.
            for fragment in document.fragments:
                old = db.execute("SELECT * FROM normalized_fragments WHERE fragment_id=?", (fragment.fragment_id,)).fetchone()
                if old and ((old["product_id"], old["source_id"], old["source_version"]) != key
                            or Fragment.model_validate_json(old["body_json"]) != fragment):
                    raise RepositoryError("FRAGMENT_CONFLICT", "Сохранённое доказательство нельзя перезаписать.")
            previous = db.execute("SELECT body_json FROM processing_results WHERE revision_id=?", (result.revision_id,)).fetchone()
            if previous and NormalizationResult.model_validate_json(previous[0]) != result:
                raise RepositoryError("REVISION_CONFLICT", "Ревизия уже сохранена с другим результатом.")
            if not previous:
                db.execute("INSERT INTO processing_results VALUES (?,?,?,?,?,?,?)",
                           (result.revision_id, *key, result.profile_hash, result.analysis_digest, result.model_dump_json()))
            old_doc = db.execute("SELECT body_json FROM normalized_documents WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()
            if not old_doc or NormalizedDocument.model_validate_json(old_doc[0]) != document:
                revision = db.execute("SELECT coalesce(max(revision),0)+1 FROM normalization_history WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()[0]
                db.execute("INSERT INTO normalization_history VALUES (?,?,?,?,?)", (*key, revision, document.model_dump_json()))
                db.execute("INSERT INTO normalized_documents VALUES (?,?,?,?) ON CONFLICT(product_id,source_id,source_version) DO UPDATE SET body_json=excluded.body_json", (*key, document.model_dump_json()))
            for fragment in document.fragments:
                db.execute("INSERT OR IGNORE INTO normalized_fragments VALUES (?,?,?,?,?)", (fragment.fragment_id, *key, fragment.model_dump_json()))
            db.execute("INSERT INTO processing_heads VALUES (?,?,?,?,NULL) ON CONFLICT(product_id,source_id,source_version) DO UPDATE SET revision_id=excluded.revision_id", (*key, result.revision_id))

    def publish_processing_needs(self, result: NormalizationResult, needs):
        result = NormalizationResult.model_validate(result.model_dump())
        view = analysis_input(result)
        needs = [ExtractedNeed.model_validate(n.model_dump()) for n in needs]
        c = result.context
        key = (c.product_id, c.source_id, c.source_version)
        fragments = {f.fragment_id: f for f in view.document.fragments} if view else {}
        if len({n.need_id for n in needs}) != len(needs) or any(
                n.source_id != c.source_id or n.fragment_id not in fragments
                or n.evidence_quote not in fragments[n.fragment_id].text for n in needs):
            raise RepositoryError("INVALID_EVIDENCE", "Анализ должен опираться на пригодные фрагменты этой ревизии.", 422)
        with self._session(write=True) as db:
            type(self)(self.path, _db=db).publish_normalization(result)
            head = db.execute("SELECT analyzed_revision FROM processing_heads WHERE product_id=? AND source_id=? AND source_version=?", key).fetchone()
            prior = db.execute("SELECT analysis_digest FROM processing_results WHERE revision_id=?", (head[0],)).fetchone() if head[0] else None
            if prior and prior[0] == result.analysis_digest:
                old_ids = {r[0] for r in db.execute("SELECT need_id FROM processing_needs WHERE revision_id=?", (head[0],))}
                if old_ids != {n.need_id for n in needs}:
                    raise RepositoryError("ANALYSIS_CONFLICT", "Точный вход уже опубликован с другим извлечением.")
            previous = {r["need_id"]: r for r in db.execute("SELECT * FROM stored_needs WHERE product_id=? AND source_id=? AND source_version=? AND active=1", key)}
            removed = previous.keys() - {n.need_id for n in needs}
            affected = {r[0] for nid in removed for r in db.execute("SELECT topic_id FROM topic_need_links WHERE need_id=? AND active=1", (nid,))}
            before_topics = {tid: self._topic(db, tid)[1] for tid in affected}
            for nid in removed:
                db.execute("UPDATE stored_needs SET active=0 WHERE need_id=?", (nid,))
                db.execute("UPDATE topic_need_links SET active=0 WHERE need_id=?", (nid,))
                db.execute("UPDATE manual_overrides SET requires_recheck=1 WHERE need_id=?", (nid,))
                db.execute("UPDATE review_records SET requires_recheck=1 WHERE review_id IN (SELECT review_id FROM review_needs WHERE need_id=?)", (nid,))
            for need in needs:
                old = db.execute("SELECT * FROM stored_needs WHERE need_id=?", (need.need_id,)).fetchone()
                if old and ((old["product_id"], old["source_id"], old["source_version"]) != key
                            or ExtractedNeed.model_validate_json(old["body_json"]) != need):
                    raise RepositoryError("NEED_ID_CONFLICT", "Сохранённую потребность нельзя перезаписать.")
                db.execute("INSERT INTO stored_needs VALUES (?,?,?,?,?,1,?) ON CONFLICT(need_id) DO UPDATE SET active=1", (need.need_id, *key, need.fragment_id, need.model_dump_json()))
                db.execute("INSERT OR IGNORE INTO processing_needs VALUES (?,?)", (result.revision_id, need.need_id))
            for tid, before in before_topics.items():
                _, current = self._topic(db, tid)
                after = current.model_copy(update={"version": before.version + 1})
                db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (after.model_dump_json(), tid))
                self._change(db, before, after, "attachment", "server", "Новая ревизия анализа: прежнее свидетельство сохранено в истории.")
            if affected:
                self._bump_catalog(db, c.product_id)
            db.execute("UPDATE processing_heads SET analyzed_revision=? WHERE product_id=? AND source_id=? AND source_version=?", (result.revision_id, *key))
            db.execute("UPDATE source_versions SET needs_extracted=1 WHERE product_id=? AND source_id=? AND version=?", key)
