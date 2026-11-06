"""Разбиение кандидатов на ограниченные по размеру пакеты."""

from __future__ import annotations

from dataclasses import dataclass

from ..retrieval import RetrievalCandidate
from ..schemas import Topic


@dataclass(frozen=True)
class PassportBatch:
    topics: tuple[Topic, ...]
    estimated_chars: int


@dataclass(frozen=True)
class BatchPlan:
    batches: tuple[PassportBatch, ...]
    total_topics: int
    included_topics: int
    complete: bool


def _topic_text(topic: Topic) -> str:
    passport = topic.passport
    parts = [
        f"topic_id: {topic.topic_id}",
        f"scenario: {passport.scenario}",
        f"need_statement: {passport.need_statement}",
        f"scope: {passport.scope}",
    ]
    parts.extend(
        f"inclusion: {example.text}"
        for example in passport.inclusion_examples
    )
    parts.extend(
        f"exclusion: {example.text}"
        for example in passport.exclusion_examples
    )
    return "\n".join(parts)


def build_batches(
    candidates: list[RetrievalCandidate] | tuple[RetrievalCandidate, ...],
    *,
    max_chars: int = 16000,
    max_batches: int = 8,
) -> BatchPlan:
    if max_chars < 1 or max_batches < 1:
        raise ValueError("Лимиты пакетов должны быть положительными")

    batches: list[PassportBatch] = []
    current: list[Topic] = []
    current_chars = 0
    included = 0

    for candidate in candidates:
        topic = candidate.topic
        topic_size = len(_topic_text(topic)) + 2

        # Один паспорт, превышающий лимит, нельзя незаметно обрезать.
        if topic_size > max_chars:
            continue

        if current and current_chars + topic_size > max_chars:
            batches.append(
                PassportBatch(tuple(current), current_chars)
            )
            current = []
            current_chars = 0

            if len(batches) >= max_batches:
                break

        current.append(topic)
        current_chars += topic_size
        included += 1

    if current and len(batches) < max_batches:
        batches.append(PassportBatch(tuple(current), current_chars))

    total = len(candidates)
    return BatchPlan(
        batches=tuple(batches),
        total_topics=total,
        included_topics=included,
        complete=included == total,
    )