"""Сервис извлечения потребностей и назначения тем."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..gateways import GatewayUnavailable, TextGateway, TextRequest
from ..retrieval import PassportRetriever
from ..schemas import (
    AssignmentResult,
    BatchAssignment,
    CatalogSnapshot,
    EvidenceRef,
    ExtractedNeed,
    ExtractionResult,
    ModelMode,
    ProcessingProfile,
    TopicAssignment,
    ProposedTopic,
)
from .extractor import NeedExtractor
from .grouping import (
    PassportBatch,
    build_batches,
    serialized_topics_size,
)
DEFAULT_ASSIGN_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "assign.md"
)
DEFAULT_PROPOSE_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "propose.md"
)

class AnalysisService:
    """Реализация Analysis для основного Pipeline."""

    def __init__(
        self,
        gateway: TextGateway,
        profile: ProcessingProfile,
        settings: Settings,
        *,
        mode: ModelMode = "replay",
        assign_prompt_path: Path | None = None,
        propose_prompt_path: Path | None = None,
    ) -> None:
        self.gateway = gateway
        self.profile = profile
        self.settings = settings
        self.mode = mode
        self.assign_prompt_path = assign_prompt_path or DEFAULT_ASSIGN_PROMPT_PATH
        self.propose_prompt_path = propose_prompt_path or DEFAULT_PROPOSE_PROMPT_PATH

        self.extractor = NeedExtractor(gateway, profile)
        self.retriever = PassportRetriever(
            k=settings.effective_retrieval_k,
            p_max=settings.app_p_max,
        )

    def extract_needs(self, document) -> ExtractionResult:
        return self.extractor.extract(document)

    def assign_topic(
        self,
        need: ExtractedNeed,
        topic_passports: CatalogSnapshot,
    ) -> AssignmentResult:
        if any(
            topic.passport.product_id != topic_passports.product_id
            for topic in topic_passports.topics
        ):
            raise ValueError(
                "Паспорт темы относится к другому product_id каталога"
            )

        prompt = self.assign_prompt_path.read_text(encoding="utf-8")

        retrieval = self.retriever.retrieve(need, topic_passports)
        oversized = [
            candidate.topic.topic_id
            for candidate in retrieval.candidates
            if serialized_topics_size([candidate.topic])
            > self.settings.app_passport_batch_max_chars
        ]
        if oversized:
            raise GatewayUnavailable(
                "ANALYSIS_BATCH_TOO_LARGE",
                "Полный JSON паспорта превышает допустимый размер: "
                + ", ".join(oversized),
            )

        plan = build_batches(
            retrieval.candidates,
            max_chars=self.settings.app_passport_batch_max_chars,
            max_batches=self.settings.app_max_assign_batches,
            max_request_tokens=getattr(
                self.settings,
                "app_max_assign_request_tokens",
                12000,
            ),
            prompt=prompt,
            need=need.model_dump(mode="json"),
            parameters=self.profile.parameters,
        )

        all_topic_ids = {topic.topic_id for topic in topic_passports.topics}
        passport_versions: dict[str, int] = {}
        candidate_ids: set[str] = set()
        metadata = []
        model_decisions: list[BatchAssignment] = []
        matched_ids: set[str] = set()

        # Для пустого каталога отправляем модели один пустой пакет:
        # это сохраняет единый контракт gateway и запись metadata.
        batches = plan.batches or (
            (PassportBatch(topics=(), estimated_chars=0),)
            if not topic_passports.topics
            else ()
        )

        for batch_number, batch in enumerate(batches, start=1):
            for topic in batch.topics:
                passport_versions[topic.topic_id] = topic.passport.version

            batch_ids = {topic.topic_id for topic in batch.topics}

            request = TextRequest(
                operation="assign_topic",
                prompt=prompt,
                prompt_version="assign-topic-v1",
                data={
                    "need": need.model_dump(mode="json"),
                    "topics": [
                        topic.model_dump(mode="json")
                        for topic in batch.topics
                    ],
                    "batch_number": batch_number,
                    "batch_count": len(batches),
                },
                parameters=self.profile.parameters,
            )

            response = self.gateway.generate(request, BatchAssignment)
            decision = response.value
            metadata.append(response.metadata)
            model_decisions.append(decision)

            unknown_ids = set(decision.candidate_topic_ids) - all_topic_ids
            if unknown_ids:
                raise ValueError(
                    "Модель вернула неизвестные topic_id: "
                    + ", ".join(sorted(unknown_ids))
                )

            if not set(decision.candidate_topic_ids).issubset(batch_ids):
                raise ValueError(
                    "Модель указала кандидата, которого не было в текущем пакете"
                )

            if decision.decision == "match":
                if decision.matched_topic_id not in batch_ids:
                    raise ValueError(
                        "matched_topic_id отсутствует в текущем пакете"
                    )
                if decision.matched_topic_id not in decision.candidate_topic_ids:
                    raise ValueError(
                        "matched_topic_id должен присутствовать в candidate_topic_ids"
                    )
                matched_ids.add(decision.matched_topic_id)

            # Модель получает только одну потребность. Поэтому допустимое
            # доказательство должно точно соответствовать её исходной цитате.
            expected_evidence = (
                need.source_id,
                need.fragment_id,
                need.evidence_quote,
            )
            if not decision.evidence_refs or any(
                (
                    ref.source_id,
                    ref.fragment_id,
                    ref.quote,
                ) != expected_evidence
                for ref in decision.evidence_refs
            ):
                raise ValueError(
                    "Некорректные evidence_refs: доказательство не совпадает "
                    "с исходной потребностью"
                )

            candidate_ids.update(decision.candidate_topic_ids)

        complete = (
            retrieval.candidate_set_complete
            and plan.complete
            and len(passport_versions) == len(topic_passports.topics)
        )

        if not plan.batches and not topic_passports.topics:
            complete = True

        # Не принимаем автоматическое совпадение, если модель назвала
        # несколько возможных тем или разные пакеты дали разные совпадения.
        ambiguous = len(candidate_ids) > 1 or len(matched_ids) > 1

        matched_id = None
        decision_name = "review"

        if (
            len(matched_ids) == 1
            and not ambiguous
            and not any(item.decision == "review" for item in model_decisions)
            and complete
        ):
            matched_id = next(iter(matched_ids))
            decision_name = "match"

        # provisional_no_match не превращаем в new: для new требуется
        # отдельный согласованный ProposedTopic и его replay-контракт.
        # Неполный поиск и отсутствие однозначного совпадения ведут на review.
        # Новая тема допустима только после полного поиска по каталогу,
        # если ни один пакет не обнаружил кандидатов или совпадений.
        all_provisional_no_match = (
            complete
            and not ambiguous
            and not candidate_ids
            and not matched_ids
            and len(model_decisions) == len(batches)
            and all(
                item.decision == "provisional_no_match"
                for item in model_decisions
            )
        )

        if all_provisional_no_match and topic_passports.topics:
            propose_prompt = self.propose_prompt_path.read_text(encoding="utf-8")
            proposal_response = self.gateway.generate(
                TextRequest(
                    operation="propose_topic",
                    prompt=propose_prompt,
                    prompt_version="propose-topic-v1",
                    data={
                        "need": need.model_dump(mode="json"),
                        "catalog_revision": topic_passports.catalog_revision,
                        "passport_versions": passport_versions,
                    },
                    parameters=self.profile.parameters,
                ),
                ProposedTopic,
            )
            proposal = ProposedTopic.model_validate(proposal_response.value)

            expected_evidence = (
                need.source_id,
                need.fragment_id,
                need.evidence_quote,
            )
            for example in proposal.inclusion_examples + proposal.exclusion_examples:
                if not example.evidence_refs or any(
                    (
                        ref.source_id,
                        ref.fragment_id,
                        ref.quote,
                    ) != expected_evidence
                    for ref in example.evidence_refs
                ):
                    raise GatewayUnavailable(
                        "PROPOSED_TOPIC_EVIDENCE_INVALID",
                        "Предложение темы содержит неподтверждённые свидетельства.",
                    )

            metadata.append(proposal_response.metadata)

            reason_parts = [
                item.reason for item in model_decisions if item.reason
            ]
            assignment = TopicAssignment(
                need_id=need.need_id,
                decision="new",
                candidate_topic_ids=[],
                reason=" ".join(reason_parts) or "Новая тема предложена после полного поиска.",
                evidence_refs=[
                    EvidenceRef(
                        source_id=need.source_id,
                        fragment_id=need.fragment_id,
                        quote=need.evidence_quote,
                    )
                ],
                passport_versions=passport_versions,
                catalog_revision=topic_passports.catalog_revision,
                total_topic_count=len(topic_passports.topics),
                searched_topic_count=len(passport_versions),
                candidate_set_complete=True,
                search_truncated=False,
                mode=self.mode,
            )
            return AssignmentResult(
                assignment=assignment,
                proposed_topic=proposal,
                metadata=metadata,
            )

        if not complete or ambiguous:
            decision_name = "review"
            matched_id = None

        reason_parts = [
            item.reason for item in model_decisions if item.reason
        ]
        reason = " ".join(reason_parts) or (
            "Каталог пуст: подходящая существующая тема не найдена."
            if not topic_passports.topics
            else "Недостаточно данных для автоматического назначения."
        )

        evidence = [
            EvidenceRef(
                source_id=need.source_id,
                fragment_id=need.fragment_id,
                quote=need.evidence_quote,
            )
        ]

        assignment = TopicAssignment(
            need_id=need.need_id,
            decision=decision_name,
            matched_topic_id=matched_id,
            candidate_topic_ids=sorted(candidate_ids),
            reason=reason,
            evidence_refs=evidence,
            passport_versions=passport_versions,
            catalog_revision=topic_passports.catalog_revision,
            total_topic_count=len(topic_passports.topics),
            searched_topic_count=len(passport_versions),
            candidate_set_complete=complete,
            search_truncated=not complete,
            mode=self.mode,
        )

        return AssignmentResult(
            assignment=assignment,
            proposed_topic=None,
            metadata=metadata,
        )