"""Детерминированный поиск кандидатов по паспортам тем."""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter

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

    # Взвешиваем совпадение уникальных терминов и повторяемость.
    matched_weight = sum(
        min(query_counts[token], passport_counts[token])
        for token in intersection
    )
    query_weight = sum(query_counts.values())
    passport_weight = sum(passport_counts.values())

    precision = matched_weight / passport_weight
    recall = matched_weight / query_weight

    # F1 не даёт длинным паспортам получать преимущество только за размер.
    score = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    # Совпадение с inclusion-примерами немного усиливает релевантность.
    inclusion_tokens = set(
        token
        for example in topic.passport.inclusion_examples
        for token in tokenize(example.text)
    )
    inclusion_bonus = (
        0.05 * len(intersection & inclusion_tokens) / max(len(intersection), 1)
    )

    # Совпадение с exclusion-примером не должно повышать релевантность.
    exclusion_tokens = set(
        token
        for example in topic.passport.exclusion_examples
        for token in tokenize(example.text)
    )
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
    """Ранжирует паспорта тем по лексической близости."""

    def __init__(self, k: int = 20, p_max: int = 50) -> None:
        if k < 1 or p_max < 1:
            raise ValueError("k и p_max должны быть положительными")
        self.k = min(k, p_max)
        self.p_max = p_max

    def retrieve(
        self,
        need: ExtractedNeed,
        snapshot: CatalogSnapshot,
    ) -> RetrievalResult:
        topics = list(snapshot.topics)
        total = len(topics)

        ranked = sorted(
            (_score(need, topic) for topic in topics),
            key=lambda item: (-item.score, item.topic.topic_id),
        )

        # Ограничиваем объём кандидатов. Полнота означает, что в выборку
        # попали все темы каталога, а не просто то, что локально всё оценили.
        selected = ranked[: self.k]
        searched = len(selected)
        complete = searched == total

        return RetrievalResult(
            candidates=tuple(selected),
            total_topic_count=total,
            searched_topic_count=searched,
            candidate_set_complete=complete,
        )