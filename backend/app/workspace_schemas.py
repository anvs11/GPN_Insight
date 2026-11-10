"""Аддитивные read-контракты мониторинга и снимков отчёта."""

from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .assessment_schemas import Category, Priority
from .schemas import Contract, Identifier, ModelMode, Text, Version, Sha256, EvidenceRef
from .weekly import CompletedWeek


class WorkspaceCard(Contract):
    kind: Literal["topic", "source"]
    id: Identifier
    version: Version
    revision: Sha256 = "0" * 64
    title: str
    summary: str
    created_at: str | None
    last_seen: str | None
    status: str
    owner: str | None = None
    ticket_count: int
    material_count: int = 0
    categories: list[Category] = Field(default_factory=list)
    priorities: list[Priority] = Field(default_factory=list)
    unassessed: bool = False
    errors: int = 0
    pending: bool = False
    synthetic: bool
    origins: list[str]
    source_ids: list[str]
    topic_ids: list[str]


class WorkspacePage(Contract):
    items: list[WorkspaceCard]
    total: int
    offset: int
    limit: int
    model_mode: ModelMode


class ReportSelection(Contract):
    kind: Literal["topic", "source"]
    id: Identifier
    version: Version = Field(strict=True)
    revision: Sha256


class ReportDraft(Contract):
    title: Text = Field(max_length=160)
    summary: str = Field(default="", max_length=4000)
    comment: str = Field(default="", max_length=8000)
    selections: list[ReportSelection] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_selection(self):
        if len({(s.kind, s.id) for s in self.selections}) != len(self.selections):
            raise ValueError("Объекты подборки должны быть уникальны")
        return self


class ArchiveEntry(Contract):
    report_id: Identifier
    title: str
    kind: Literal["selection", "weekly"]
    generated_at: AwareDatetime
    model_mode: ModelMode
    period: CompletedWeek | None
    selection_count: int = Field(ge=0)


class ArchivePage(Contract):
    items: list[ArchiveEntry]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class WeeklySchedule(Contract):
    enabled: bool
    timezone: Literal["Europe/Moscow"]
    next_run_at: AwareDatetime
    last_error: Literal["WEEKLY_ARCHIVE_FAILED"] | None
    catch_up: Literal["latest_completed_week"]


class ReportSnapshot(ArchiveEntry):
    format_version: Literal["report-snapshot-v1"]
    digest: Sha256
    markdown: str
    summary: str
    comment: str
    limitations: str
    cards: list[WorkspaceCard]
    selections: list[ReportSelection]
    evidence: list[list[EvidenceRef]]
    source_versions: dict[str, Version]
    distinct_source_count: int | None
