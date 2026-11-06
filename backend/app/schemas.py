"""Общие контракты v1. Проверка файлов и доказательств выполняется сервисами."""

from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Version = Annotated[int, Field(ge=1, strict=True)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ModelMode = Literal["live", "replay"]
Origin = Literal["model", "authored"]
TopicStatus = Literal[
    "new", "needs_research", "linked", "deferred", "declined", "implemented", "merged"
]
BusinessStatus = Literal["needs_research", "linked", "deferred", "declined", "implemented"]


class Contract(BaseModel):
    # Исходный текст и цитаты сохраняются точно, без скрытого strip().
    model_config = ConfigDict(extra="forbid")


class MaterialInput(Contract):
    asset_id: Identifier
    kind: Literal["audio", "image"]
    path: Annotated[str, Field(min_length=1, max_length=255)]
    mime_type: Literal["audio/wav", "audio/mpeg", "image/png", "image/jpeg"]
    source_url: str | None = None

    @model_validator(mode="after")
    def check_kind(self) -> Self:
        if not self.mime_type.startswith("audio/" if self.kind == "audio" else "image/"):
            raise ValueError("Вид материала не соответствует MIME-типу")
        # Это ограничение формата manifest, не проверка безопасности файла.
        if self.path in {".", ".."} or any(c in self.path for c in "/\\:"):
            raise ValueError("В path нужно простое имя файла без каталогов")
        return self


class TicketInput(Contract):
    source_id: Identifier
    product_id: Identifier = "webneft"
    created_at: AwareDatetime
    text: str = ""
    requester_id: Identifier | None = None
    assets: list[MaterialInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_assets(self) -> Self:
        ids = [a.asset_id for a in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset_id внутри обращения должны быть уникальны")
        return self


class MaterialError(Contract):
    source_id: Identifier
    asset_id: Identifier | None = None
    stage: Text
    code: Text
    message: Text


class Fragment(Contract):
    fragment_id: Identifier
    asset_id: Identifier | None = None
    kind: Literal["text", "asr", "vision"]
    text: str
    start_ms: Annotated[int, Field(ge=0)] | None = None
    end_ms: Annotated[int, Field(ge=0)] | None = None
    warning: str | None = None

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if (self.kind == "text") != (self.asset_id is None):
            raise ValueError("Текст не имеет asset_id; ASR/VL должны ссылаться на материал")
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("Временные метки задаются парой")
        if self.start_ms is not None:
            if self.kind != "asr" or self.end_ms <= self.start_ms:
                raise ValueError("Допустим только положительный интервал ASR")
        return self


class NormalizedDocument(Contract):
    product_id: Identifier = "webneft"
    source_id: Identifier
    content_hash: Sha256
    version: Version
    normalization_version: Text
    fragments: list[Fragment]
    errors: list[MaterialError] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_fragments(self) -> Self:
        ids = [f.fragment_id for f in self.fragments]
        if len(ids) != len(set(ids)):
            raise ValueError("fragment_id должны быть уникальны")
        if any(e.source_id != self.source_id for e in self.errors):
            raise ValueError("Ошибка должна относиться к этому источнику")
        return self


class EvidenceRef(Contract):
    source_id: Identifier
    fragment_id: Identifier
    quote: Text


class ExtractedNeed(Contract):
    need_id: Identifier
    source_id: Identifier
    fragment_id: Identifier
    evidence_quote: Text
    scenario: Text
    need_type: Literal["problem", "question", "operational_request", "feature_request"]
    statement: Text
    explicit_request: bool
    improvement_hypothesis: Text | None = None
    uncertainty_reason: Text | None = None


class PassportExample(Contract):
    text: Text
    evidence_refs: Annotated[list[EvidenceRef], Field(min_length=1)]


class TopicPassport(Contract):
    product_id: Identifier = "webneft"
    scenario: Text
    need_statement: Text
    scope: Text
    inclusion_examples: list[PassportExample]
    exclusion_examples: list[PassportExample] = Field(default_factory=list)
    scope_confirmed: bool = False
    version: Version


class BatchAssignment(Contract):
    """Ответ модели: серверные признаки полноты здесь запрещены."""

    decision: Literal["match", "review", "provisional_no_match"]
    matched_topic_id: Identifier | None = None
    candidate_topic_ids: list[Identifier] = Field(default_factory=list)
    reason: Text
    evidence_refs: Annotated[list[EvidenceRef], Field(min_length=1)]

    @model_validator(mode="after")
    def match_target(self) -> Self:
        if (self.decision == "match") != (self.matched_topic_id is not None):
            raise ValueError("matched_topic_id задаётся только для match")
        return self


class ProposedTopic(Contract):
    """Содержимое новой темы; постоянство и бизнес-поля назначает сервер."""

    title: Text
    summary: Text
    scenario: Text
    need_statement: Text
    scope: Text
    inclusion_examples: Annotated[list[PassportExample], Field(min_length=1)]
    exclusion_examples: list[PassportExample] = Field(default_factory=list)


class TopicAssignment(Contract):
    """Итог сервера, а не допустимая схема ответа модели."""

    need_id: Identifier
    decision: Literal["match", "review", "new"]
    matched_topic_id: Identifier | None = None
    candidate_topic_ids: list[Identifier] = Field(default_factory=list)
    reason: Text
    evidence_refs: Annotated[list[EvidenceRef], Field(min_length=1)]
    passport_versions: dict[str, Version]
    catalog_revision: Annotated[int, Field(ge=0)]
    total_topic_count: Annotated[int, Field(ge=0)]
    searched_topic_count: Annotated[int, Field(ge=0)]
    candidate_set_complete: bool
    search_truncated: bool
    mode: ModelMode

    @model_validator(mode="after")
    def completeness(self) -> Self:
        if self.searched_topic_count > self.total_topic_count:
            raise ValueError("Число проверенных тем превышает размер каталога")
        if self.search_truncated == self.candidate_set_complete:
            raise ValueError("Признаки полноты поиска должны быть противоположны")
        if self.candidate_set_complete and self.searched_topic_count != self.total_topic_count:
            raise ValueError("Полный поиск требует проверки всего снимка")
        if (self.decision == "match") != (self.matched_topic_id is not None):
            raise ValueError("matched_topic_id задаётся только для match")
        if self.decision == "new" and not self.candidate_set_complete:
            raise ValueError("new запрещён при неполном поиске")
        return self


class ImportCounts(Contract):
    inserted: Annotated[int, Field(ge=0)] = 0
    updated: Annotated[int, Field(ge=0)] = 0
    unchanged: Annotated[int, Field(ge=0)] = 0
    invalid: Annotated[int, Field(ge=0)] = 0


DatasetId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class ImportIssue(Contract):
    code: Text
    message: Text
    source_id: Identifier | None = None
    asset_id: Identifier | None = None
    field: Text | None = None


class ImportPreview(Contract):
    # При неразбираемом manifest идентификатор набора ещё неизвестен.
    dataset_id: DatasetId | None = None
    status: Literal["valid", "invalid"]
    counts: ImportCounts
    errors: list[ImportIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    can_commit: bool

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        valid = not self.errors and self.counts.invalid == 0
        if self.can_commit != valid or (self.status == "valid") != valid:
            raise ValueError("Статус preview не согласован с ошибками")
        if valid and self.dataset_id is None:
            raise ValueError("Корректный набор должен иметь dataset_id")
        return self


class ImportResult(Contract):
    dataset_id: DatasetId
    counts: ImportCounts
    imported_at: AwareDatetime

    @model_validator(mode="after")
    def committed(self) -> Self:
        if self.counts.invalid:
            raise ValueError("Успешный атомарный импорт не содержит invalid")
        return self


class ReviewResolution(Contract):
    action: Literal["attach", "create", "insufficient_data", "merge", "keep_separate"]
    reason: Text
    author: Text
    resolved_at: AwareDatetime
    topic_id: Identifier | None = None
    request_hash: Sha256 | None = None


class ReviewItem(Contract):
    review_id: Identifier
    version: Version
    kind: Literal["assignment", "duplicate"]
    need_ids: list[Identifier] = Field(default_factory=list)
    topic_ids: list[Identifier] = Field(default_factory=list)
    candidate_topic_ids: list[Identifier] = Field(default_factory=list)
    reason: Text
    evidence_refs: list[EvidenceRef]
    status: Literal["open", "resolved", "insufficient_data"] = "open"
    resolution: ReviewResolution | None = None
    requires_recheck: bool = False

    @model_validator(mode="after")
    def check_resolution(self) -> Self:
        if self.kind == "assignment" and (not self.need_ids or self.topic_ids):
            raise ValueError("Assignment review требует потребности, а не пару тем")
        if self.kind == "duplicate" and (len(self.topic_ids) != 2 or len(set(self.topic_ids)) != 2 or self.need_ids):
            raise ValueError("Duplicate review требует две разные темы")
        if (self.status == "open") != (self.resolution is None):
            raise ValueError("Закрытый review требует решения; открытый его не имеет")
        if self.resolution:
            action = self.resolution.action
            allowed = {"attach", "create", "insufficient_data"} if self.kind == "assignment" else {"merge", "keep_separate"}
            if action not in allowed:
                raise ValueError("Действие не соответствует виду review")
            if (self.status == "insufficient_data") != (action == "insufficient_data"):
                raise ValueError("Статус review не соответствует решению")
            if action in {"attach", "merge"} and not self.resolution.topic_id:
                raise ValueError("Для действия нужна целевая тема")
        return self


class DecisionFields(Contract):
    owner: Text
    reason: Text
    existing_issue_ref: Text | None = None
    review_after: date | None = None
    implemented_at: AwareDatetime | None = None
    implementation_comment: Text | None = None

    def check_status_fields(self, status: str) -> None:
        if status == "linked" and not self.existing_issue_ref:
            raise ValueError("Для передачи в разработку нужна ссылка или идентификатор задачи")
        if status == "deferred" and self.review_after is None:
            raise ValueError("Для отложенной темы нужна дата пересмотра")
        if status == "implemented" and (self.implemented_at is None or not self.implementation_comment):
            raise ValueError("Для реализации нужны дата и описание результата")


class DecisionUpdate(DecisionFields):
    status: BusinessStatus | Literal["new"]
    version: Version

    @model_validator(mode="after")
    def required_fields(self) -> Self:
        self.check_status_fields(self.status)
        return self


class DecisionEvent(DecisionFields):
    event_id: Identifier
    topic_id: Identifier
    previous_status: TopicStatus
    new_status: TopicStatus
    occurred_at: AwareDatetime
    topic_version: Version

    @model_validator(mode="after")
    def required_fields(self) -> Self:
        self.check_status_fields(self.new_status)
        return self


class Topic(Contract):
    topic_id: Identifier
    passport: TopicPassport
    title: Text
    summary: Text
    need_ids: list[Identifier]
    evidence: list[EvidenceRef]
    status: TopicStatus = "new"
    owner: Text | None = None
    version: Version = 1
    canonical_topic_id: Identifier | None = None

    @model_validator(mode="after")
    def canonical(self) -> Self:
        if (self.status == "merged") != (self.canonical_topic_id is not None):
            raise ValueError("Только merged содержит canonical_topic_id")
        if self.canonical_topic_id == self.topic_id:
            raise ValueError("Тема не может ссылаться сама на себя")
        if len(self.need_ids) != len(set(self.need_ids)):
            raise ValueError("Потребности темы должны быть уникальны")
        return self


class TopicStats(Contract):
    ticket_count: Annotated[int, Field(ge=0)]
    requester_count: Annotated[int, Field(ge=0)] | None
    weekly_counts: dict[str, int]
    first_seen: AwareDatetime | None
    last_seen: AwareDatetime | None
    support_level: Literal["single", "repeated"]
    new_ticket_count_since_decision: Annotated[int, Field(ge=0)]
    post_implementation_ticket_count: Annotated[int, Field(ge=0)]
    review_due: bool


class SourceDetail(Contract):
    ticket: TicketInput
    document: NormalizedDocument | None
    needs: list[ExtractedNeed]
    source_version: Version = 1
    content_hash: Sha256 | None = None
    requires_analysis: bool = False
    synthetic: bool = True
    data_origin: Literal["authored", "imported"] = "authored"


class TopicDetail(Contract):
    topic: Topic
    stats: TopicStats
    sources: list[SourceDetail]
    history: list[DecisionEvent]
    model_mode: ModelMode
    analysis_mode: ModelMode | None = None
    synthetic: bool = True
    data_origin: Origin = "authored"
    pending_source_ids: list[Identifier] = Field(default_factory=list)


class RecordingMeta(Contract):
    request_hash: Sha256
    prompt_hash: Sha256 | None = None
    normalization_hash: Sha256
    model_id: Text
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    origin: Origin
    recording_set_version: Text


class Run(Contract):
    run_id: Identifier
    product_id: Identifier = "webneft"
    mode: ModelMode
    state: Literal["queued", "running", "completed", "completed_with_errors", "failed", "interrupted"]
    stage: Text
    successful_materials: Annotated[int, Field(ge=0)] = 0
    failed_materials: Annotated[int, Field(ge=0)] = 0
    total_materials: Annotated[int, Field(ge=0)] = 0
    coverage: Annotated[float, Field(ge=0, le=1)] | None = None
    errors: list[MaterialError] = Field(default_factory=list)
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    origins: list[Origin] = Field(default_factory=list)
    completed_steps: Annotated[int, Field(ge=0)] = 0
    reused_steps: Annotated[int, Field(ge=0)] = 0
    failure_code: Text | None = None
    failure_message: Text | None = None

    @model_validator(mode="after")
    def coverage_from_counts(self) -> Self:
        if (self.failure_code is None) != (self.failure_message is None):
            raise ValueError("Общая ошибка запуска требует кода и сообщения")
        if self.successful_materials + self.failed_materials > self.total_materials:
            raise ValueError("Обработанных материалов больше, чем всего материалов")
        expected = self.successful_materials / self.total_materials if self.total_materials else None
        if self.coverage != expected:
            raise ValueError("Покрытие должно вычисляться по успешным материалам")
        if self.started_at and self.started_at < self.created_at:
            raise ValueError("Начало запуска раньше его создания")
        if self.finished_at and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("Окончание запуска раньше его начала")
        return self


class ModelAvailability(Contract):
    status: Literal["available", "unavailable", "not_checked", "not_configured", "not_implemented"]
    configured: bool
    checked_at: AwareDatetime | None = None
    code: Text
    message: Text


class Health(Contract):
    status: Literal["ok"] = "ok"
    model_mode: ModelMode
    processing_state: Literal["idle", "busy", "not_implemented"] = "not_implemented"
    available_models: dict[Literal["text", "asr", "vision"], ModelAvailability]
    replay_origins: list[Origin] = Field(default_factory=list)
    replay_record_count: Annotated[int, Field(ge=0)] = 0
    pipeline_ready: bool = False
    integration_errors: list[str] = Field(default_factory=list)


class ErrorResponse(Contract):
    code: Text
    message: Text
    run_id: Identifier | None = None


class RegisteredMaterial(Contract):
    """Метаданные уже проверенного/перемещённого файла; файловых операций нет."""

    asset_id: Identifier
    kind: Literal["audio", "image"]
    content_hash: Sha256
    storage_path: Text
    original_filename: Text
    mime_type: Literal["audio/wav", "audio/mpeg", "image/png", "image/jpeg"]
    byte_size: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def server_path(self) -> Self:
        path = PurePosixPath(self.storage_path)
        if path.is_absolute() or any(p in {".", ".."} for p in self.storage_path.split("/")) or any(c in self.storage_path for c in "\\:"):
            raise ValueError("storage_path должен быть серверным относительным путём внутри uploads")
        MaterialInput(asset_id=self.asset_id, kind=self.kind, path=self.original_filename, mime_type=self.mime_type)
        return self


class DatasetRegistration(Contract):
    dataset_id: DatasetId
    product_id: Identifier = "webneft"
    synthetic: bool
    origin: Literal["authored", "imported"] = "imported"
    tickets: list[TicketInput]
    materials: list[RegisteredMaterial] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self) -> Self:
        ids = [t.source_id for t in self.tickets]
        assets = [a for t in self.tickets for a in t.assets]
        asset_ids = [a.asset_id for a in assets]
        prepared = {m.asset_id: m for m in self.materials}
        if len(ids) != len(set(ids)) or any(t.product_id != self.product_id for t in self.tickets):
            raise ValueError("Источники набора должны быть уникальны и относиться к одному продукту")
        if len(asset_ids) != len(set(asset_ids)) or len(prepared) != len(self.materials) or set(asset_ids) != prepared.keys():
            raise ValueError("Каждому уникальному материалу manifest нужны ровно одни подготовленные метаданные")
        for asset in assets:
            material = prepared[asset.asset_id]
            if (asset.kind, asset.path, asset.mime_type) != (material.kind, material.original_filename, material.mime_type):
                raise ValueError("Метаданные подготовленного материала не совпадают с manifest")
        return self


class ImportLogEntry(Contract):
    registration_id: Identifier
    product_id: Identifier
    result: ImportResult
    synthetic: bool


class ManualOverride(Contract):
    override_id: Identifier
    product_id: Identifier = "webneft"
    source_id: Identifier
    source_version: Version
    need_id: Identifier
    action: Literal["attach", "exclude", "insufficient_data"]
    topic_id: Identifier | None = None
    author: Text
    reason: Text
    created_at: AwareDatetime
    requires_recheck: bool = False

    @model_validator(mode="after")
    def target(self) -> Self:
        if self.action in {"attach", "exclude"} and self.topic_id is None:
            raise ValueError("Ручное назначение/исключение требует тему")
        if self.action == "insufficient_data" and self.topic_id is not None:
            raise ValueError("Недостаточность данных не задаёт тему")
        return self


class TopicChangeEvent(Contract):
    event_id: Identifier
    topic_id: Identifier
    kind: Literal["passport", "attachment", "manual_override", "merge", "exclusion"]
    author: Text
    reason: Text
    occurred_at: AwareDatetime
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]
    request_hash: Sha256 | None = None
    result: dict[str, JsonValue] | None = None


class CatalogSnapshot(Contract):
    product_id: Identifier
    catalog_revision: Annotated[int, Field(ge=0)]
    topics: list[Topic]


class RunRequest(Contract):
    product_id: Literal["webneft"] = "webneft"
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def period(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Начало периода позже его конца")
        return self


class ProcessingProfile(Contract):
    model_id: Text
    normalization_version: Text
    prompt_hashes: dict[str, Sha256]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    recording_set_version: Text

    @model_validator(mode="after")
    def required_prompt_hashes(self) -> Self:
        if not {"extract_needs", "assign_topic"}.issubset(self.prompt_hashes):
            raise ValueError("Нужны хеши содержимого промптов извлечения и сопоставления")
        return self


class ExtractedNeeds(Contract):
    """Текстовый ответ извлечения; служебные metadata не принимает от модели."""
    needs: list[ExtractedNeed]


class ExtractionResult(Contract):
    needs: list[ExtractedNeed]
    metadata: list[RecordingMeta]


class AssignmentResult(Contract):
    assignment: TopicAssignment
    # Сохраняем чтение checkpoints и старых серверных адаптеров.
    new_topic: Topic | None = None
    proposed_topic: ProposedTopic | None = None
    metadata: list[RecordingMeta]

    @model_validator(mode="after")
    def new_topic_payload(self) -> Self:
        payloads = int(self.new_topic is not None) + int(self.proposed_topic is not None)
        if payloads != int(self.assignment.decision == "new"):
            raise ValueError("Для new нужен ровно один proposed_topic либо прежний new_topic")
        return self


class ResolveReviewRequest(Contract):
    version: Version
    action: Literal["attach", "create", "insufficient_data"]
    author: Text
    reason: Text
    topic_versions: dict[str, Version] = Field(default_factory=dict)
    topic_id: Identifier | None = None
    proposed_topic: ProposedTopic | None = None

    @model_validator(mode="after")
    def action_payload(self) -> Self:
        if (self.action == "attach") != (self.topic_id is not None):
            raise ValueError("topic_id нужен только для attach")
        if (self.action == "create") != (self.proposed_topic is not None):
            raise ValueError("proposed_topic нужен только для create")
        return self


class MergePreviewRequest(Contract):
    target_topic_id: Identifier


class MergePreview(Contract):
    source: TopicDetail
    target: TopicDetail
    decision_conflict: bool


class MergeTopicsRequest(Contract):
    version: Version
    target_topic_id: Identifier
    target_version: Version
    author: Text
    reason: Text
    confirm_decision_conflict: Annotated[bool, Field(strict=True)] = False


class MergeResult(Contract):
    source_topic_id: Identifier
    target_topic_id: Identifier
    source_version: Version
    target_version: Version


class ExcludeNeedsRequest(Contract):
    version: Version
    need_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    author: Text
    reason: Text

    @model_validator(mode="after")
    def unique_needs(self) -> Self:
        if len(set(self.need_ids)) != len(self.need_ids):
            raise ValueError("Потребности не должны повторяться")
        return self


class ExcludeResult(Contract):
    topic_id: Identifier
    topic_version: Version
    need_ids: list[Identifier]
    review_ids: list[Identifier]
