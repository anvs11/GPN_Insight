"""Оценка категории/приоритета — отдельный versioned результат, не бизнес-статус."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .schemas import Contract, EvidenceRef, Identifier, RecordingMeta, Sha256, Text

Category = Literal["suspected_defect", "usability_confusion", "improvement_request", "operational_request", "unclassified"]
Priority = Literal["normal", "elevated", "potentially_critical", "unknown"]


class NeedAssessment(Contract):
    category: Category
    priority: Priority
    reason: Text
    uncertainties: list[Text] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def uncertainty(self):
        if self.priority in {"unknown", "potentially_critical"} and not self.uncertainties:
            raise ValueError("Unknown/critical сохраняют неизвестности и ограничения вывода")
        return self


class AssessmentResult(Contract):
    format_version: Literal["need-assessment-v1"] = "need-assessment-v1"
    need_id: Identifier
    analysis_digest: Sha256
    assessment: NeedAssessment
    metadata: RecordingMeta


class TopicAssessmentSummary(Contract):
    format_version: Literal["topic-assessment-summary-v1"] = "topic-assessment-summary-v1"
    topic_id: Identifier
    topic_version: Annotated[int, Field(ge=1, strict=True)]
    unit: Literal["sources"] = "sources"
    source_count: Annotated[int, Field(ge=0, strict=True)]
    unassessed_source_count: Annotated[int, Field(ge=0, strict=True)]
    categories: dict[Category, Annotated[int, Field(ge=0, strict=True)]]
    priorities: dict[Priority, Annotated[int, Field(ge=0, strict=True)]]
    aggregation: Literal["distinct-source-per-value; categories/priorities overlap; no inferred normal priority"]
