"""Ручные операции: короткие транзакции, без моделей и политики auto-match."""

from uuid import uuid4

from .replay import semantic_hash, stable_id
from .repositories import Repository, RepositoryError
from .schemas import (
    DecisionEvent, EvidenceRef, ExcludeNeedsRequest, ExcludeResult, ExtractedNeed,
    ManualOverride, MergePreview, MergeResult, MergeTopicsRequest, ModelMode,
    ResolveReviewRequest, ReviewItem, ReviewResolution, Topic, TopicChangeEvent, TopicPassport,
)
from .storage import now


class HumanWorkflow:
    def __init__(self, repository: Repository):
        self.repo = repository

    @staticmethod
    def _command_hash(operation, identity, command):
        return semantic_hash({"operation": operation, "id": identity, "request": command.model_dump(mode="json")})

    @staticmethod
    def _needs(tx, need_ids):
        if not need_ids or len(need_ids) != len(set(need_ids)):
            raise RepositoryError("NEEDS_INVALID", "Нужны разные потребности текущей версии источника.", 422)
        rows = []
        for need_id in need_ids:
            row = tx._db.execute("SELECT * FROM stored_needs WHERE need_id=?", (need_id,)).fetchone()
            if row is None:
                raise RepositoryError("NEED_NOT_FOUND", "Потребность не найдена. Обновите данные.", 404)
            source = tx._source(tx._db, row["product_id"], row["source_id"])
            if not row["active"] or source["current_version"] != row["source_version"]:
                raise RepositoryError("SOURCE_VERSION_CONFLICT", "Источник изменился. Требуется проверка новой версии.")
            if row["product_id"] != "webneft":
                raise RepositoryError("PRODUCT_MISMATCH", "Операция доступна только для продукта Вебнефть.", 422)
            rows.append(row)
        return rows

    @staticmethod
    def _linked_topics(tx, need_ids):
        return {row[0] for need_id in need_ids for row in tx._db.execute(
            "SELECT topic_id FROM topic_need_links WHERE need_id=? AND active=1", (need_id,))}

    @staticmethod
    def _versions(tx, expected, affected):
        if set(expected) != set(affected):
            raise RepositoryError("VERSION_CONFLICT", "Передайте актуальные версии всех затронутых тем без лишних ID.")
        for topic_id in affected:
            _, topic = tx._topic(tx._db, topic_id)
            if topic.status == "merged" or topic.version != expected[topic_id]:
                raise RepositoryError("VERSION_CONFLICT", "Одна из тем изменилась. Обновите карточки.")

    @staticmethod
    def _override(tx, row, action, topic_id, author, reason):
        affected = HumanWorkflow._linked_topics(tx, [row["need_id"]])
        if topic_id:
            affected.add(topic_id)
        versions = {t: tx._topic(tx._db, t)[1].version for t in affected}
        tx.record_override(ManualOverride(override_id=str(uuid4()), product_id=row["product_id"],
            source_id=row["source_id"], source_version=row["source_version"], need_id=row["need_id"],
            action=action, topic_id=topic_id, author=author, reason=reason, created_at=now()), expected_versions=versions)

    def resolve_review(self, review_id: str, command: ResolveReviewRequest) -> ReviewItem:
        command = ResolveReviewRequest.model_validate(command.model_dump())
        request_hash = self._command_hash("resolve-review", review_id, command)
        with self.repo.transaction() as tx:
            review = tx.get_review(review_id)
            if review.resolution and review.resolution.request_hash == request_hash:
                return review  # Подтверждённый повтор не применяет действие заново.
            if review.version != command.version or review.status != "open" or review.requires_recheck:
                raise RepositoryError("VERSION_CONFLICT", "Review изменён, закрыт или требует проверки новой версии.")
            if review.kind != "assignment":
                raise RepositoryError("FEATURE_NOT_IMPLEMENTED", "Разрешение duplicate review пока не подключено.", 501)
            rows = self._needs(tx, review.need_ids)
            expected_refs = {(n.source_id, n.fragment_id, n.evidence_quote)
                             for n in (ExtractedNeed.model_validate_json(row["body_json"]) for row in rows)}
            if {(ref.source_id, ref.fragment_id, ref.quote) for ref in review.evidence_refs} != expected_refs:
                raise RepositoryError("INVALID_EVIDENCE", "Источники review не соответствуют его текущим потребностям.", 422)
            affected = self._linked_topics(tx, review.need_ids)
            if command.topic_id:
                _, target = tx._topic(tx._db, command.topic_id)
                if target.passport.product_id != rows[0]["product_id"]:
                    raise RepositoryError("PRODUCT_MISMATCH", "Выберите тему того же продукта.", 422)
                affected.add(command.topic_id)
            self._versions(tx, command.topic_versions, affected)
            target_id = command.topic_id
            if command.action == "create":
                needs = [ExtractedNeed.model_validate_json(row["body_json"]) for row in rows]
                refs = [EvidenceRef(source_id=n.source_id, fragment_id=n.fragment_id, quote=n.evidence_quote) for n in needs]
                allowed = {(r.source_id, r.fragment_id, r.quote) for r in refs}
                p = command.proposed_topic
                if any((r.source_id, r.fragment_id, r.quote) not in allowed
                       for example in p.inclusion_examples + p.exclusion_examples for r in example.evidence_refs):
                    raise RepositoryError("INVALID_EVIDENCE", "Примеры должны подтверждаться потребностями этого review.", 422)
                for row in rows:
                    if self._linked_topics(tx, [row["need_id"]]):
                        self._override(tx, row, "insufficient_data", None, command.author, command.reason)
                target_id = stable_id("manual-topic", review_id)
                topic = Topic(topic_id=target_id, title=p.title, summary=p.summary,
                    passport=TopicPassport(product_id=rows[0]["product_id"], scenario=p.scenario, need_statement=p.need_statement,
                        scope=p.scope, inclusion_examples=p.inclusion_examples, exclusion_examples=p.exclusion_examples, version=1),
                    need_ids=review.need_ids, evidence=refs)
                tx.create_topic(topic, origin="authored", manual_author=command.author, manual_reason=command.reason)
            else:
                for row in rows:
                    self._override(tx, row, command.action, target_id, command.author, command.reason)
            resolution = ReviewResolution(action=command.action, author=command.author, reason=command.reason,
                resolved_at=now(), topic_id=target_id, request_hash=request_hash)
            saved = ReviewItem.model_validate({**review.model_dump(), "version": review.version + 1,
                "status": "insufficient_data" if command.action == "insufficient_data" else "resolved",
                "resolution": resolution.model_dump()})
            tx.save_review(saved, expected_version=command.version)
            return tx.get_review(review_id)

    @staticmethod
    def _receipt(tx, topic_id, kind, request_hash, schema):
        for change in tx.topic_changes(topic_id):
            if change.kind == kind and change.request_hash == request_hash and change.result is not None:
                return schema.model_validate(change.result)
        return None

    @staticmethod
    def _event(tx, before, after, kind, command, request_hash, result):
        event = TopicChangeEvent(event_id=str(uuid4()), topic_id=before.topic_id, kind=kind,
            author=command.author, reason=command.reason, occurred_at=now(), before=before.model_dump(mode="json"),
            after=after.model_dump(mode="json"), request_hash=request_hash, result=result.model_dump(mode="json"))
        tx._db.execute("INSERT INTO topic_changes VALUES (?,?,?)", (event.event_id, before.topic_id, event.model_dump_json()))

    def exclude_needs(self, topic_id: str, command: ExcludeNeedsRequest) -> ExcludeResult:
        command = ExcludeNeedsRequest.model_validate(command.model_dump())
        request_hash = self._command_hash("exclude", topic_id, command)
        with self.repo.transaction() as tx:
            before = tx._topic(tx._db, topic_id)[1]
            receipt = self._receipt(tx, topic_id, "exclusion", request_hash, ExcludeResult)
            if receipt:
                return receipt
            self._versions(tx, {topic_id: command.version}, {topic_id})
            rows = self._needs(tx, command.need_ids)
            if not set(command.need_ids).issubset(before.need_ids):
                raise RepositoryError("NEED_NOT_ATTACHED", "Выберите только текущие потребности этой темы.", 422)
            review_ids = []
            for row in rows:
                self._override(tx, row, "exclude", topic_id, command.author, command.reason)
                need = ExtractedNeed.model_validate_json(row["body_json"])
                review_id = stable_id("exclusion-review", topic_id, need.need_id, request_hash)
                tx.save_review(ReviewItem(review_id=review_id, version=1, kind="assignment", need_ids=[need.need_id],
                    reason=command.reason, evidence_refs=[EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id,
                        quote=need.evidence_quote)]))
                review_ids.append(review_id)
            after = tx._topic(tx._db, topic_id)[1]
            result = ExcludeResult(topic_id=topic_id, topic_version=after.version, need_ids=command.need_ids, review_ids=review_ids)
            self._event(tx, before, after, "exclusion", command, request_hash, result)
            return result

    @staticmethod
    def _merge_cards(tx, source_id, target_id, mode):
        if source_id == target_id:
            raise RepositoryError("MERGE_SELF", "Нельзя объединить тему с собой.", 422)
        source = tx.get_topic(source_id, mode)
        target = tx.get_topic(target_id, mode)
        if source.topic.status == "merged" or target.topic.status == "merged":
            raise RepositoryError("TOPIC_MERGED", "Выберите две действующие основные темы.")
        if source.topic.passport.product_id != target.topic.passport.product_id:
            raise RepositoryError("PRODUCT_MISMATCH", "Темы разных продуктов объединять нельзя.", 422)
        def decision(card):
            if not card.history:
                return None
            return card.history[-1].model_dump(exclude={"event_id", "topic_id", "topic_version", "occurred_at", "previous_status"})
        conflict = bool(source.history and decision(source) != decision(target))
        return MergePreview(source=source, target=target, decision_conflict=conflict)

    def merge_preview(self, source_id: str, target_id: str, mode: ModelMode) -> MergePreview:
        with self.repo.transaction() as tx:
            return self._merge_cards(tx, source_id, target_id, mode)

    def merge_topics(self, source_id: str, command: MergeTopicsRequest, mode: ModelMode) -> MergeResult:
        command = MergeTopicsRequest.model_validate(command.model_dump())
        request_hash = self._command_hash("merge", source_id, command)
        with self.repo.transaction() as tx:
            receipt = self._receipt(tx, source_id, "merge", request_hash, MergeResult)
            if receipt:
                return receipt
            preview = self._merge_cards(tx, source_id, command.target_topic_id, mode)
            source, target = preview.source.topic, preview.target.topic
            self._versions(tx, {source_id: command.version, target.topic_id: command.target_version}, {source_id, target.topic_id})
            if preview.decision_conflict and not command.confirm_decision_conflict:
                raise RepositoryError("MERGE_DECISION_CONFLICT", "Решения различаются. Проверьте обе карточки и подтвердите сохранение решения целевой темы.")
            rows = self._needs(tx, source.need_ids) if source.need_ids else []
            for row in rows:
                self._override(tx, row, "attach", target.topic_id, command.author, command.reason)
            # Сохраняем исторические ссылки для provenance/pending; старые версии не активируем.
            tx._db.execute("INSERT INTO topic_need_links(topic_id,need_id,active) SELECT ?,need_id,0 FROM topic_need_links WHERE topic_id=? "
                "ON CONFLICT(topic_id,need_id) DO NOTHING", (target.topic_id, source_id))
            tx._db.execute("UPDATE topic_need_links SET active=0 WHERE topic_id=?", (source_id,))
            source_after = tx._topic(tx._db, source_id)[1]
            source_after = source_after.model_copy(update={"status": "merged", "canonical_topic_id": target.topic_id,
                "version": source_after.version + 1})
            target_after = tx._topic(tx._db, target.topic_id)[1]
            if target_after.version == target.version:
                target_after = target_after.model_copy(update={"version": target.version + 1})
                tx._db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (target_after.model_dump_json(), target.topic_id))
            tx._db.execute("UPDATE topics SET body_json=? WHERE topic_id=?", (source_after.model_dump_json(), source_id))
            tx._db.execute("UPDATE topics SET synthetic=?,origin=?,analysis_mode=? WHERE topic_id=?", (
                int(preview.source.synthetic or preview.target.synthetic),
                "authored" if "authored" in {preview.source.data_origin, preview.target.data_origin} else "model",
                preview.target.analysis_mode if preview.target.analysis_mode == preview.source.analysis_mode else None, target.topic_id))
            event = DecisionEvent(event_id=str(uuid4()), topic_id=source_id, previous_status=source.status,
                new_status="merged", owner=command.author, reason=command.reason, occurred_at=now(), topic_version=source_after.version)
            tx._db.execute("INSERT INTO decision_events VALUES (?,?,?,?)", (event.event_id, source_id, event.topic_version, event.model_dump_json()))
            tx._bump_catalog(tx._db, source.passport.product_id)
            result = MergeResult(source_topic_id=source_id, target_topic_id=target.topic_id,
                source_version=source_after.version, target_version=target_after.version)
            self._event(tx, source, source_after, "merge", command, request_hash, result)
            self._event(tx, target, target_after, "merge", command, request_hash, result)
            return result
