"""Детерминированный поиск кандидатов по всем паспортам тем."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .schemas import CatalogSnapshot, ExtractedNeed, Topic


_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

_STOP_WORDS = {
    "и", "в", "во", "на", "по", "для", "с", "со", "из", "к", "ко",
    "у", "о", "об", "от", "до", "за", "при", "не", "что", "как",
    "это", "а", "но", "или", "бы", "же", "ли", "мы", "вы", "они",
    "он", "она", "оно", "их", "его", "ее", "её", "мне", "нам",
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on",
    "with", "is", "are", "be", "this", "that",
}


@dataclass(frozen=True)
class RetrievalCandidate:
    topic: Topic
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[RetrievalCandidate, ...]
    total_topic_count: int
    searched_topic_count: int
    candidate_set_complete: bool


def tokenize(value: str) -> list[str]:
    """Выделяет значимые слова из текста."""
    return [
        token.lower()
        for token in _WORD_RE.findall(value.lower())
        if len(token) > 1 and token.lower() not in _STOP_WORDS
    ]


def _passport_text(topic: Topic) -> str:
    passport = topic.passport
    parts = [
        passport.scenario,
        passport.need_statement,
        passport.scope,
    ]
    parts.extend(example.text for example in passport.inclusion_examples)
    parts.extend(example.text for example in passport.exclusion_examples)
    return " ".join(parts)


def _score(need: ExtractedNeed, topic: Topic) -> RetrievalCandidate:
    """Считает прозрачную лексическую близость потребности и паспорта."""
    query_tokens = tokenize(
        f"{need.scenario} {need.statement} {need.evidence_quote}"
    )
    passport_tokens = tokenize(_passport_text(topic))

    query_counts = Counter(query_tokens)
    passport_counts = Counter(passport_tokens)

    if not query_counts or not passport_counts:
        return RetrievalCandidate(topic, 0.0, ())

    intersection = set(query_counts) & set(passport_counts)
    matched_weight = sum(
        min(query_counts[token], passport_counts[token])
        for token in intersection
    )
    query_weight = sum(query_counts.values())
    passport_weight = sum(passport_counts.values())

    precision = matched_weight / passport_weight
    recall = matched_weight / query_weight
    score = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    inclusion_tokens = {
        token
        for example in topic.passport.inclusion_examples
        for token in tokenize(example.text)
    }
    inclusion_bonus = (
        0.05 * len(intersection & inclusion_tokens) / max(len(intersection), 1)
    )

    exclusion_tokens = {
        token
        for example in topic.passport.exclusion_examples
        for token in tokenize(example.text)
    }
    exclusion_penalty = (
        0.10 * len(intersection & exclusion_tokens) / max(len(intersection), 1)
    )

    final_score = max(0.0, min(1.0, score + inclusion_bonus - exclusion_penalty))
    return RetrievalCandidate(
        topic=topic,
        score=final_score,
        matched_terms=tuple(sorted(intersection)),
    )


class PassportRetriever:
    """Ранжирует весь каталог; ограничение числа пакетов применяется позже."""

    def __init__(self, k: int = 20, p_max: int = 50) -> None:
        if k < 1 or p_max < 1:
            raise ValueError("k и p_max должны быть положительными")
        # k оставлен для совместимости настроек и старых вызовов.
        # Он больше не обрезает каталог: полнота поиска важнее shortlist.
        self.k = k
        self.p_max = p_max

    def retrieve(
        self,
        need: ExtractedNeed,
        snapshot: CatalogSnapshot,
    ) -> RetrievalResult:
        topics = list(snapshot.topics)
        ranked = sorted(
            (_score(need, topic) for topic in topics),
            key=lambda item: (-item.score, item.topic.topic_id),
        )
        return RetrievalResult(
            candidates=tuple(ranked),
            total_topic_count=len(topics),
            searched_topic_count=len(ranked),
            candidate_set_complete=True,
        )
