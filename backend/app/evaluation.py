"""Метрики качества извлечения и назначения потребностей."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ClassificationMetrics:
    total: int
    correct: int
    accuracy: float
    coverage: float


@dataclass(frozen=True)
class ExtractionMetrics:
    expected_count: int
    actual_count: int
    matched_count: int
    precision: float
    recall: float
    f1: float


def classification_metrics(
    expected: Iterable[str],
    actual: Iterable[str],
) -> ClassificationMetrics:
    expected_values = list(expected)
    actual_values = list(actual)

    if len(expected_values) != len(actual_values):
        raise ValueError(
            "Количество эталонных и предсказанных меток должно совпадать"
        )

    if not expected_values:
        return ClassificationMetrics(0, 0, 0.0, 0.0)

    correct = sum(
        expected_label == actual_label
        for expected_label, actual_label in zip(
            expected_values, actual_values, strict=True
        )
    )

    covered = sum(label not in {"", "unlabeled"} for label in actual_values)

    return ClassificationMetrics(
        total=len(expected_values),
        correct=correct,
        accuracy=correct / len(expected_values),
        coverage=covered / len(actual_values),
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def extraction_metrics(
    expected_statements: Iterable[str],
    actual_statements: Iterable[str],
) -> ExtractionMetrics:
    """Сравнивает извлечённые формулировки по точному нормализованному тексту.

    Это базовая воспроизводимая метрика. Семантическое сравнение
    можно добавить отдельно после выбора и согласования модели.
    """
    expected = Counter(_normalize(value) for value in expected_statements)
    actual = Counter(_normalize(value) for value in actual_statements)

    matched = sum((expected & actual).values())
    expected_count = sum(expected.values())
    actual_count = sum(actual.values())

    precision = matched / actual_count if actual_count else 0.0
    recall = matched / expected_count if expected_count else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    return ExtractionMetrics(
        expected_count=expected_count,
        actual_count=actual_count,
        matched_count=matched,
        precision=precision,
        recall=recall,
        f1=f1,
    )