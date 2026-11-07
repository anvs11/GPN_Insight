from types import SimpleNamespace

from app.analysis.grouping import (
    build_batches,
    estimate_assignment_request_tokens,
    estimate_tokens,
)


def make_candidate(topic_id: str, title: str = "Тема"):
    topic = SimpleNamespace(
        topic_id=topic_id,
        model_dump=lambda mode="json": {
            "topic_id": topic_id,
            "title": title,
        },
    )
    return SimpleNamespace(topic=topic)


def test_estimate_tokens_accounts_for_utf8_cyrillic():
    text = "Потребность в техническом обслуживании"

    assert estimate_tokens(text) == (
        len(text.encode("utf-8")) + 1
    ) // 2


def test_request_token_estimate_includes_prompt_need_and_parameters():
    topic = make_candidate("topic-1").topic

    base = estimate_assignment_request_tokens(
        prompt="",
        need={},
        topics=[topic],
    )
    full = estimate_assignment_request_tokens(
        prompt="Подробная инструкция модели " * 100,
        need={"description": "Описание потребности " * 50},
        topics=[topic],
        parameters={"temperature": 0},
    )

    assert full > base


def test_build_batches_marks_topic_incomplete_when_full_request_exceeds_budget():
    candidate = make_candidate("topic-1")

    plan = build_batches(
        [candidate],
        max_chars=100_000,
        max_batches=8,
        max_request_tokens=10,
        prompt="Большой системный промпт",
        need={"description": "Потребность"},
    )

    assert plan.complete is False
    assert plan.included_topics == 0
    assert plan.batches == ()


def test_build_batches_splits_topics_by_full_request_budget():
    candidates = [
        make_candidate("topic-1", "Первая тема"),
        make_candidate("topic-2", "Вторая тема"),
    ]

    one_topic_budget = estimate_assignment_request_tokens(
        prompt="Короткий промпт",
        need={"description": "Потребность"},
        topics=[candidates[0].topic],
        batch_number=8,
        batch_count=8,
    )

    plan = build_batches(
        candidates,
        max_chars=100_000,
        max_batches=8,
        max_request_tokens=one_topic_budget,
        prompt="Короткий промпт",
        need={"description": "Потребность"},
    )

    assert plan.complete is True
    assert plan.included_topics == 2
    assert len(plan.batches) == 2
    assert all(
        batch.estimated_tokens <= one_topic_budget
        for batch in plan.batches
    )