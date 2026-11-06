"""Правила передачи неоднозначных назначений на ручную проверку."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .schemas import TopicAssignment


ReviewReason = Literal[
    "model_review",
    "incomplete_search",
    "ambiguous_candidates",
    "unconfirmed_scope",
    "low_retrieval_coverage",
]


@dataclass(frozen=True)
class ReviewAssessment:
    required: bool
    reasons: tuple[ReviewReason, ...]


def assess_assignment(
    assignment: TopicAssignment,
    *,
    minimum_candidates: int = 1,
) -> ReviewAssessment:
    """Проверяет, можно ли принять назначение автоматически."""

    reasons: list[ReviewReason] = []

    if not assignment.candidate_set_complete:
        reasons.append("incomplete_search")

    if assignment.decision == "review":
        reasons.append("model_review")

    if len(assignment.candidate_topic_ids) > minimum_candidates:
        reasons.append("ambiguous_candidates")

    if assignment.decision == "new" and not assignment.candidate_set_complete:
        # Защитная проверка на случай вызова вне основного Pipeline.
        reasons.append("incomplete_search")

    return ReviewAssessment(
        required=bool(reasons),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def should_auto_accept(assignment: TopicAssignment) -> bool:
    """Автоматически принимаем только однозначное назначение в существующую тему."""
    return (
        assignment.decision == "match"
        and assignment.candidate_set_complete
        and len(assignment.candidate_topic_ids) <= 1
    )