"""Разбиение паспортов на пакеты с оценкой полного запроса."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ..retrieval import RetrievalCandidate
from ..schemas import Topic


@dataclass(frozen=True)
class PassportBatch:
    topics: tuple[Topic, ...]
    estimated_chars: int
    estimated_tokens: int = 0


@dataclass(frozen=True)
class BatchPlan:
    batches: tuple[PassportBatch, ...]
    total_topics: int
    included_topics: int
    complete: bool


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _serialized_topics(topics: list[Topic] | tuple[Topic, ...]) -> str:
    return _json([topic.model_dump(mode="json") for topic in topics])


def serialized_topics_size(topics: list[Topic] | tuple[Topic, ...]) -> int:
    """Размер полного JSON-массива паспортов в символах."""
    return len(_serialized_topics(topics))


def estimate_tokens(text: str) -> int:
    """Консервативная оценка токенов без привязки к конкретному токенизатору.

    Для кириллицы оценка по UTF-8 байтам существенно надёжнее,
    чем деление количества символов на среднее для английского текста.
    """
    return math.ceil(len(text.encode("utf-8")) / 2)


def estimate_assignment_request_tokens(
    *,
    prompt: str,
    need: dict[str, Any],
    topics: list[Topic] | tuple[Topic, ...],
    batch_number: int = 1,
    batch_count: int = 8,
    parameters: dict[str, Any] | None = None,
) -> int:
    """Оценивает prompt и весь JSON data, передаваемый assign_topic."""
    data = {
        "need": need,
        "topics": [topic.model_dump(mode="json") for topic in topics],
        "batch_number": batch_number,
        "batch_count": batch_count,
    }
    parameters_text = _json(parameters or {})
    request_text = prompt + _json(data) + parameters_text
    return estimate_tokens(request_text)


def build_batches(
    candidates: list[RetrievalCandidate] | tuple[RetrievalCandidate, ...],
    *,
    max_chars: int = 16000,
    max_batches: int = 8,
    max_request_tokens: int = 12000,
    prompt: str = "",
    need: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
) -> BatchPlan:
    if min(max_chars, max_batches, max_request_tokens) < 1:
        raise ValueError("Лимиты пакетов должны быть положительными")

    need_data = need or {}
    batches: list[PassportBatch] = []
    current: list[Topic] = []
    included = 0
    stopped_by_limit = False

    def fits(topics: list[Topic]) -> tuple[bool, int, int]:
        chars = len(_serialized_topics(topics))
        tokens = estimate_assignment_request_tokens(
            prompt=prompt,
            need=need_data,
            topics=topics,
            batch_number=max_batches,
            batch_count=max_batches,
            parameters=parameters,
        )
        return chars <= max_chars and tokens <= max_request_tokens, chars, tokens

    def append_current() -> None:
        nonlocal current
        if not current:
            return
        _, chars, tokens = fits(current)
        batches.append(
            PassportBatch(
                topics=tuple(current),
                estimated_chars=chars,
                estimated_tokens=tokens,
            )
        )
        current = []

    for candidate in candidates:
        topic = candidate.topic
        single_fits, _, _ = fits([topic])

        # Отдельный паспорт не помещается даже в пустой запрос.
        # Не теряем это обстоятельство: complete останется False.
        if not single_fits:
            append_current()
            continue

        trial = current + [topic]
        trial_fits, _, _ = fits(trial)

        if trial_fits:
            current = trial
            included += 1
            continue

        append_current()

        if len(batches) >= max_batches:
            stopped_by_limit = True
            break

        current = [topic]
        included += 1

    append_current()

    if len(batches) > max_batches:
        batches = batches[:max_batches]
        stopped_by_limit = True

    total = len(candidates)
    complete = included == total and not stopped_by_limit

    return BatchPlan(
        batches=tuple(batches),
        total_topics=total,
        included_topics=included,
        complete=complete,
    )