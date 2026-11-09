"""Версионированная partial/media граница; legacy model-facing DTO не меняются."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .coverage_schemas import MaterialCoverage, SourceCoverage
from .assessment_schemas import AssessmentResult
from .replay import semantic_hash
from .schemas import (
    Contract, Fragment, Identifier, MaterialError, ModelMode, NormalizedDocument,
    RecordingMeta, Sha256, Text, Version,
)

Count = Annotated[int, Field(ge=0, strict=True)]
Category = Literal["suspected_defect", "usability_confusion", "improvement_request", "operational_request", "unclassified"]
Priority = Literal["normal", "elevated", "potentially_critical", "unknown"]


class MediaComponentProfile(Contract):
    model_id: Text
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    prompt: Text | None = None
    prompt_version: Text | None = None

    @model_validator(mode="after")
    def paired_prompt(self):
        if (self.prompt is None) != (self.prompt_version is None):
            raise ValueError("Промпт и версия задаются вместе")
        semantic_hash(self.parameters)
        return self


class MediaProcessingProfile(Contract):
    format_version: Literal["media-profile-v1"] = "media-profile-v1"
    mode: ModelMode = "replay"
    normalization_version: Literal["media-pii-v1"] = "media-pii-v1"
    normalization_hash: Sha256
    recording_set_version: Text
    asr: MediaComponentProfile
    vision: MediaComponentProfile


class MediaRequest(Contract):
    format_version: Literal["media-request-v1"] = "media-request-v1"
    operation: Literal["transcribe", "describe"]
    asset_id: Identifier
    content_hash: Sha256
    byte_size: Annotated[int, Field(gt=0, strict=True)]
    mime_type: Literal["audio/wav", "audio/mpeg", "image/png", "image/jpeg"]
    model: MediaComponentProfile
    normalization_hash: Sha256
    recording_set_version: Text

    @model_validator(mode="after")
    def operation_type(self):
        if (self.operation == "transcribe") != self.mime_type.startswith("audio/"):
            raise ValueError("Операция не соответствует MIME")
        return self


class SourceContext(Contract):
    product_id: Identifier = "webneft"
    source_id: Identifier
    source_version: Version
    content_hash: Sha256
    synthetic: Annotated[bool, Field(strict=True)]
    data_origin: Literal["imported", "authored"]


class MaterialOutcome(Contract):
    asset_id: Identifier | None = None
    kind: Literal["text", "audio", "image"]
    input_hash: Sha256
    state: Literal["success", "empty", "failed"]
    fragments: list[Fragment] = Field(default_factory=list)
    error: MaterialError | None = None
    metadata: RecordingMeta | None = None

    @model_validator(mode="after")
    def consistent(self):
        if (self.kind == "text") != (self.asset_id is None):
            raise ValueError("Текст и вложение имеют разную идентичность")
        if self.state == "failed":
            if self.error is None or self.fragments or self.metadata is not None or self.error.asset_id != self.asset_id:
                raise ValueError("Failed сохраняет только ошибку своего материала")
        else:
            if self.error is not None or (self.kind != "text") != (self.metadata is not None):
                raise ValueError("Успех медиа требует metadata, ошибки запрещены")
            usable = any(f.text.strip() for f in self.fragments)
            if usable != (self.state == "success"):
                raise ValueError("Пустой результат отличается от содержательного успеха")
        expected_kind = {"text": "text", "audio": "asr", "image": "vision"}[self.kind]
        if any(f.asset_id != self.asset_id or f.kind != expected_kind for f in self.fragments):
            raise ValueError("Фрагмент принадлежит другому материалу")
        if len({f.fragment_id for f in self.fragments}) != len(self.fragments):
            raise ValueError("Повтор fragment_id")
        return self



def material_coverage(materials: list[MaterialOutcome]) -> MaterialCoverage:
    failed = sum(m.state == "failed" for m in materials)
    empty = sum(m.state == "empty" for m in materials)
    total = len(materials)
    return MaterialCoverage(total=total, successful=total - failed, failed=failed, empty=empty,
                            usable=total - failed - empty, ratio=(total - failed) / total if total else None)


class AnalysisInput(Contract):
    """Success-проекция, но source_errors/coverage остаются явными вне текста модели."""
    format_version: Literal["partial-analysis-v1"] = "partial-analysis-v1"
    revision_id: Sha256
    analysis_digest: Sha256
    profile_hash: Sha256
    document: NormalizedDocument
    source_errors: list[MaterialError]
    coverage: MaterialCoverage

    @model_validator(mode="after")
    def usable(self):
        failed_assets = {e.asset_id for e in self.source_errors}
        if (self.document.errors or not any(f.text.strip() for f in self.document.fragments)
                or len(self.source_errors) != self.coverage.failed
                or any(e.source_id != self.document.source_id for e in self.source_errors)
                or any(f.asset_id in failed_assets for f in self.document.fragments)
                or len({f.asset_id for f in self.document.fragments}) != self.coverage.usable
                or self.analysis_digest != semantic_hash(dict(profile_hash=self.profile_hash, document=self.document.model_dump(mode="json")))):
            raise ValueError("Анализ получает только пригодные success fragments")
        return self


class NormalizationResult(Contract):
    format_version: Literal["partial-normalization-v1"] = "partial-normalization-v1"
    context: SourceContext
    profile_hash: Sha256
    document: NormalizedDocument
    materials: list[MaterialOutcome]
    coverage: MaterialCoverage
    state: Literal["complete", "partial", "failed", "empty"]
    revision_id: Sha256
    analysis_digest: Sha256

    @model_validator(mode="after")
    def consistent(self):
        c, d = self.context, self.document
        if (d.product_id, d.source_id, d.version, d.content_hash) != (c.product_id, c.source_id, c.source_version, c.content_hash):
            raise ValueError("Документ не соответствует исходной версии")
        keys = [(m.kind == "text", m.asset_id) for m in self.materials]
        if not self.materials or len(keys) != len(set(keys)):
            raise ValueError("Нужны уникальные исходы материалов")
        if d.fragments != [f for m in self.materials for f in m.fragments] or d.errors != [m.error for m in self.materials if m.error]:
            raise ValueError("Нельзя скрыть ошибку или добавить фрагмент вне success outcome")
        if self.coverage != material_coverage(self.materials) or self.state != result_state(self.coverage):
            raise ValueError("Исход и покрытие не соответствуют материалам")
        if self.revision_id != semantic_hash(self.model_dump(mode="json", exclude={"revision_id", "analysis_digest"})):
            raise ValueError("Revision не соответствует неизменному результату")
        if self.analysis_digest != semantic_hash(dict(profile_hash=self.profile_hash, document=analysis_document(d).model_dump(mode="json"))):
            raise ValueError("Digest аналитического входа неверен")
        return self


def analysis_document(document: NormalizedDocument) -> NormalizedDocument:
    # Вызывается только после проверки всего NormalizationResult; исходник не меняется.
    return NormalizedDocument.model_validate({**document.model_dump(), "errors": [],
        "fragments": [f.model_dump() for f in document.fragments if f.text.strip()]})


def result_state(coverage: MaterialCoverage) -> str:
    if coverage.failed:
        return "partial" if coverage.successful else "failed"
    return "complete" if coverage.usable else "empty"


def make_normalization_result(context: SourceContext, profile_hash: str, version: str,
                              materials: list[MaterialOutcome]) -> NormalizationResult:
    materials = [MaterialOutcome.model_validate(m.model_dump()) for m in materials]
    doc = NormalizedDocument(product_id=context.product_id, source_id=context.source_id, version=context.source_version,
        content_hash=context.content_hash, normalization_version=version,
        fragments=[f for m in materials for f in m.fragments], errors=[m.error for m in materials if m.error])
    coverage = material_coverage(materials)
    values = dict(format_version="partial-normalization-v1", context=context.model_dump(mode="json"), profile_hash=profile_hash,
                  document=doc.model_dump(mode="json"), materials=[m.model_dump(mode="json") for m in materials],
                  coverage=coverage.model_dump(mode="json"), state=result_state(coverage))
    return NormalizationResult(**values, revision_id=semantic_hash(values),
        analysis_digest=semantic_hash(dict(profile_hash=profile_hash, document=analysis_document(doc).model_dump(mode="json"))))


def analysis_input(result: NormalizationResult) -> AnalysisInput | None:
    result = NormalizationResult.model_validate(result.model_dump())
    if not result.coverage.usable:
        return None
    return AnalysisInput(revision_id=result.revision_id, analysis_digest=result.analysis_digest, profile_hash=result.profile_hash,
        document=analysis_document(result.document), source_errors=result.document.errors, coverage=result.coverage)


class ProcessingDetail(Contract):
    manual_recheck_required: bool = False
    format_version: Literal["partial-source-v1"] = "partial-source-v1"
    result: NormalizationResult
    current_head_revision: Sha256 | None
    is_current: bool
    analysis_pending: bool | None
    assessments: dict[Identifier, AssessmentResult | None]
    unassessed_need_ids: list[Identifier]
