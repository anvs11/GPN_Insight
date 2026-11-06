"""Последовательность и транзакции; извлечение/группировка приходят от владельцев."""

from dataclasses import dataclass
from threading import Event
from typing import Protocol

from .errors import RepositoryError
from .gateways import GatewayUnavailable
from .replay import semantic_hash, stable_id
from .schemas import (
    AssignmentResult, CatalogSnapshot, EvidenceRef, ExtractedNeed, ExtractionResult, MaterialError,
    NormalizedDocument, ProcessingProfile, ReviewItem, Run, SourceDetail, TicketInput, Topic, TopicPassport,
)
from .storage import now


class Normalizer(Protocol):
    def normalize_ticket(self, ticket: TicketInput, *, content_hash: str, source_version: int) -> NormalizedDocument: ...


class Analysis(Protocol):
    def extract_needs(self, document: NormalizedDocument) -> ExtractionResult: ...
    def assign_topic(self, need: ExtractedNeed, topic_passports: CatalogSnapshot) -> AssignmentResult: ...


@dataclass(frozen=True)
class PipelineAdapters:
    normalizer: Normalizer
    analysis: Analysis
    profile: ProcessingProfile


class RunStopped(Exception):
    pass


class Pipeline:
    def __init__(self, repository, adapters: PipelineAdapters, stop: Event):
        self.repo = repository
        self.adapters = adapters
        self.stop = stop

    def _ensure_running(self, tx, run_id):
        if self.stop.is_set() or tx.get_run(run_id).state != "running":
            raise RunStopped()

    def execute(self, run_id: str) -> None:
        inputs = self.repo.get_run_inputs(run_id)
        sources = [SourceDetail.model_validate(s) for s in inputs["sources"]]
        profile = ProcessingProfile.model_validate(inputs["profile"])
        if profile != self.adapters.profile:
            raise RepositoryError("PROCESSING_PROFILE_CHANGED", "Профиль обработки изменился; создайте новый запуск.")
        old = self.repo.get_run(run_id)
        run = Run.model_validate({**old.model_dump(), "state": "running", "stage": "extract", "started_at": now()})
        self.repo.save_run(run)
        errors = []
        success = failed = completed = reused = 0
        origins = set()
        ordered_needs = []

        def update(stage):
            with self.repo.transaction() as tx:
                self._ensure_running(tx, run_id)
                current = tx.get_run(run_id)
                tx.save_run(Run.model_validate({**current.model_dump(), "stage": stage, "successful_materials": success,
                    "failed_materials": failed, "coverage": success / current.total_materials if current.total_materials else None,
                    "errors": errors, "origins": sorted(origins), "completed_steps": completed, "reused_steps": reused}))

        def error(source_id, stage, exc, asset_id=None):
            if isinstance(exc, RunStopped):
                raise exc
            code = exc.code if isinstance(exc, (RepositoryError, GatewayUnavailable)) else "PIPELINE_STAGE_FAILED"
            errors.append(MaterialError(source_id=source_id, asset_id=asset_id, stage=stage, code=code,
                message="Этап не завершён. Проверьте код ошибки и интеграцию; успешные шаги сохранены для повтора."))

        def key(stage, source, need_id=None):
            return semantic_hash({"stage": stage, "product": source.ticket.product_id, "source": source.ticket.source_id,
                "version": source.source_version, "content_hash": source.content_hash, "need_id": need_id,
                "mode": run.mode, "profile": profile.model_dump(mode="json")})

        def check_metadata(metadata, operation, *, proposal=False):
            if not metadata:
                raise RepositoryError("ANALYSIS_PROVENANCE_MISSING", "Анализ должен сообщить происхождение результатов.", 422)
            expected_hashes = {profile.prompt_hashes[operation]}
            if proposal:
                if "propose_topic" not in profile.prompt_hashes:
                    raise RepositoryError("PROPOSAL_PROFILE_MISSING", "Для новой темы нужен профиль с хешем propose_topic.", 422)
                expected_hashes.add(profile.prompt_hashes["propose_topic"])
                if len(metadata) < 2:
                    raise RepositoryError("ANALYSIS_PROVENANCE_MISSING", "Нужны ответы поиска и предложения новой темы.", 422)
            for meta in metadata:
                if (meta.model_id != profile.model_id or meta.recording_set_version != profile.recording_set_version or
                    meta.prompt_hash not in expected_hashes or (run.mode == "live" and meta.origin != "model")):
                    raise RepositoryError("ANALYSIS_PROVENANCE_CONFLICT", "Происхождение анализа не совпало с профилем запуска.", 422)
                origins.add(meta.origin)
            if not expected_hashes.issubset({m.prompt_hash for m in metadata}):
                raise RepositoryError("ANALYSIS_PROVENANCE_MISSING", "Отсутствует происхождение обязательного этапа анализа.", 422)

        # Первый проход целиком заканчивается до любого назначения.
        for source in sources:
            source_id = source.ticket.source_id
            if source.ticket.assets:
                failed += 1 + len(source.ticket.assets)
                error(source_id, "normalization", GatewayUnavailable("MEDIA_PIPELINE_NOT_IMPLEMENTED", "Медиа не подключены"))
                update("extract")
                continue
            try:
                normalized_key = key("normalization", source)
                checkpoint = self.repo.checkpoint(normalized_key)
                if checkpoint:
                    document = NormalizedDocument.model_validate(checkpoint["document"])
                    reused += 1
                else:
                    document = NormalizedDocument.model_validate(self.adapters.normalizer.normalize_ticket(
                        source.ticket, content_hash=source.content_hash, source_version=source.source_version).model_dump())
                    if (document.product_id, document.source_id, document.version, document.content_hash) != (
                        source.ticket.product_id, source_id, source.source_version, source.content_hash):
                        raise RepositoryError("NORMALIZATION_IDENTITY_CONFLICT", "Нормализация относится к другому источнику/версии.", 422)
                    if document.normalization_version != profile.normalization_version:
                        raise RepositoryError("NORMALIZATION_PROFILE_CONFLICT", "Версия нормализации не совпадает с профилем запуска.", 422)
                    document = document.model_copy(update={"fragments": [f.model_copy(update={"fragment_id": stable_id(
                        "fragment", document.product_id, source_id, source.source_version, source.content_hash, i)}) for i, f in enumerate(document.fragments)]})
                    with self.repo.transaction() as tx:
                        self._ensure_running(tx, run_id)
                        tx.save_normalized_document(document)
                        if not document.errors:
                            tx.save_checkpoint(normalized_key, "normalization", run_id, {"document": document.model_dump(mode="json")})
                    completed += 1
                if document.errors:
                    errors.extend(document.errors)
                    failed += 1
                    error(source_id, "extraction", GatewayUnavailable("EXTRACTION_MATERIAL_ERRORS", "Анализ ошибочного материала пропущен."))
                    update("extract")
                    continue
                else:
                    success += 1
            except Exception as exc:
                error(source_id, "normalization", exc)
                failed += 1
                update("extract")
                continue
            try:
                extraction_key = key("extraction", source)
                checkpoint = self.repo.checkpoint(extraction_key)
                if checkpoint:
                    extraction = ExtractionResult.model_validate(checkpoint)
                    reused += 1
                else:
                    extraction = ExtractionResult.model_validate(self.adapters.analysis.extract_needs(document).model_dump())
                    extraction = extraction.model_copy(update={"needs": [n.model_copy(update={"need_id": stable_id(
                        "need", document.product_id, source_id, source.source_version, source.content_hash, i)}) for i, n in enumerate(extraction.needs)]})
                    check_metadata(extraction.metadata, "extract_needs")
                    with self.repo.transaction() as tx:
                        self._ensure_running(tx, run_id)
                        tx.save_needs(document.product_id, source_id, source.source_version, extraction.needs)
                        tx.save_checkpoint(extraction_key, "extraction", run_id, extraction.model_dump(mode="json"))
                    completed += 1
                check_metadata(extraction.metadata, "extract_needs")
                ordered_needs.extend((source, need) for need in extraction.needs)
            except Exception as exc:
                error(source_id, "extraction", exc)
            update("extract")

        update("assignment")
        for source, need in ordered_needs:
            assignment_key = key("assignment", source, need.need_id)
            try:
                checkpoint = self.repo.checkpoint(assignment_key)
                if checkpoint:
                    if "manual_review_id" not in checkpoint:
                        saved = AssignmentResult.model_validate(checkpoint)
                        check_metadata(saved.metadata, "assign_topic", proposal=saved.proposed_topic is not None)
                    reused += 1
                else:
                    overrides = self.repo.get_overrides(source.ticket.source_id, source.ticket.product_id)
                    if any(o.need_id == need.need_id and not o.requires_recheck for o in overrides):
                        # Ручной результат остаётся действующим, pipeline его не отменяет.
                        reused += 1
                        update("assignment")
                        continue
                    if any(o.requires_recheck for o in overrides):
                        # Новая версия источника не отменяет прежнее ручное решение.
                        with self.repo.transaction() as tx:
                            self._ensure_running(tx, run_id)
                            snapshot = tx.catalog_snapshot(source.ticket.product_id)
                            current_topics = {t.topic_id for t in snapshot.topics}
                            candidates = sorted({o.topic_id for o in overrides if o.topic_id in current_topics})
                            review_id = stable_id("manual-recheck", need.need_id, assignment_key)
                            tx.save_review(ReviewItem(review_id=review_id, version=1, kind="assignment", need_ids=[need.need_id],
                                candidate_topic_ids=candidates, reason="Источник изменился после ручного решения. Проверьте назначение для новой версии.",
                                evidence_refs=[EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)]))
                            tx.save_checkpoint(assignment_key, "assignment", run_id, {"manual_review_id": review_id})
                        completed += 1
                        update("assignment")
                        continue
                    snapshot = self.repo.catalog_snapshot(source.ticket.product_id)
                    result = AssignmentResult.model_validate(self.adapters.analysis.assign_topic(need, snapshot).model_dump())
                    check_metadata(result.metadata, "assign_topic", proposal=result.proposed_topic is not None)
                    a = result.assignment
                    actual = {t.topic_id: t.passport.version for t in snapshot.topics}
                    refs = {(r.source_id, r.fragment_id, r.quote) for r in a.evidence_refs}
                    if (a.need_id != need.need_id or a.mode != run.mode or a.catalog_revision != snapshot.catalog_revision or
                        a.total_topic_count != len(actual) or not set(a.candidate_topic_ids).issubset(actual) or
                        a.searched_topic_count != len(a.passport_versions) or
                        (a.candidate_set_complete and set(a.passport_versions) != set(actual)) or
                        any(actual.get(k) != v for k, v in a.passport_versions.items()) or
                        (a.decision == "new" and (set(a.passport_versions) != set(actual) or a.candidate_topic_ids)) or
                        (a.decision == "match" and (a.matched_topic_id not in a.passport_versions or a.matched_topic_id not in a.candidate_topic_ids)) or
                        refs != {(need.source_id, need.fragment_id, need.evidence_quote)}):
                        raise RepositoryError("ASSIGNMENT_INVALID", "Назначение не соответствует источнику или снимку каталога.", 422)
                    with self.repo.transaction() as tx:
                        self._ensure_running(tx, run_id)
                        if tx.catalog_snapshot(source.ticket.product_id).catalog_revision != snapshot.catalog_revision:
                            raise RepositoryError("CATALOG_CHANGED", "Каталог изменился; повторите назначение в следующем запуске.")
                        if a.decision == "match":
                            target = tx.get_topic(a.matched_topic_id, run.mode).topic
                            tx.attach_needs(target.topic_id, [need.need_id], expected_version=target.version)
                        elif a.decision == "new":
                            if result.proposed_topic is not None:
                                proposed = result.proposed_topic
                                examples = proposed.inclusion_examples + proposed.exclusion_examples
                                if any((ref.source_id, ref.fragment_id, ref.quote) != (
                                    need.source_id, need.fragment_id, need.evidence_quote)
                                    for example in examples for ref in example.evidence_refs):
                                    raise RepositoryError("NEW_TOPIC_INVALID", "Примеры новой темы должны ссылаться на назначаемую потребность.", 422)
                                topic = Topic(topic_id=stable_id("topic", need.need_id), title=proposed.title, summary=proposed.summary,
                                    passport=TopicPassport(product_id=source.ticket.product_id, scenario=proposed.scenario,
                                        need_statement=proposed.need_statement, scope=proposed.scope,
                                        inclusion_examples=proposed.inclusion_examples, exclusion_examples=proposed.exclusion_examples,
                                        version=1, scope_confirmed=False),
                                    need_ids=[need.need_id], evidence=[EvidenceRef(source_id=need.source_id,
                                        fragment_id=need.fragment_id, quote=need.evidence_quote)])
                            else:
                                topic = result.new_topic.model_copy(update={"topic_id": stable_id("topic", need.need_id)})
                            if topic.need_ids != [need.need_id] or topic.passport.product_id != source.ticket.product_id:
                                raise RepositoryError("NEW_TOPIC_INVALID", "Новая тема должна относиться к назначаемой потребности.", 422)
                            tx.create_topic(topic, origin="authored" if "authored" in {m.origin for m in result.metadata} else "model", analysis_mode=run.mode)
                            if result.proposed_topic is None:
                                result = result.model_copy(update={"new_topic": topic})
                        else:
                            tx.save_review(ReviewItem(review_id=stable_id("review", need.need_id, assignment_key), version=1,
                                kind="assignment", need_ids=[need.need_id], candidate_topic_ids=a.candidate_topic_ids,
                                reason=a.reason, evidence_refs=a.evidence_refs))
                        tx.save_checkpoint(assignment_key, "assignment", run_id, result.model_dump(mode="json"))
                    completed += 1
            except Exception as exc:
                error(source.ticket.source_id, "assignment", exc)
            update("assignment")
        with self.repo.transaction() as tx:
            self._ensure_running(tx, run_id)
            current = tx.get_run(run_id)
            tx.save_run(Run.model_validate({**current.model_dump(), "state": "completed_with_errors" if errors else "completed",
                "stage": "finished", "finished_at": now()}))
