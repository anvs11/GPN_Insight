from types import SimpleNamespace

import pytest

from app.evaluation import (
    classification_metrics,
    extraction_metrics,
)
from app.review import assess_assignment, should_auto_accept
from app.retrieval import PassportRetriever, tokenize
from app.analysis.grouping import build_batches
from app.schemas import (
    EvidenceRef,
    ExtractedNeed,
    PassportExample,
    Topic,
    TopicPassport,
    CatalogSnapshot,
)


def make_need() -> ExtractedNeed:
    return ExtractedNeed(
        need_id="need-1",
        source_id="source-1",
        fragment_id="fragment-1",
        evidence_quote="Не удается выгрузить отчет по скважине",
        scenario="Подготовка отчетности",
        need_type="problem",
        statement="Пользователь не может выгрузить отчет по скважине",
        explicit_request=True,
    )


def make_topic(topic_id: str, text: str) -> Topic:
    ref = EvidenceRef(
        source_id="source-seed",
        fragment_id="fragment-seed",
        quote=text,
    )
    passport = TopicPassport(
        scenario="Подготовка отчетности",
        need_statement=text,
        scope="Выгрузка и формирование отчетов",
        inclusion_examples=[
            PassportExample(text=text, evidence_refs=[ref])
        ],
        version=1,
    )
    return Topic(
        topic_id=topic_id,
        passport=passport,
        title=f"Тема {topic_id}",
        summary=text,
        need_ids=[],
        evidence=[ref],
        status="new",
        version=1,
    )


def make_snapshot(*topics: Topic) -> CatalogSnapshot:
    return CatalogSnapshot(
        product_id="webneft",
        catalog_revision=1,
        topics=list(topics),
    )


def test_tokenize_removes_common_stop_words():
    result = tokenize("Не удается выгрузить отчет для скважины")
    assert "выгрузить" in result
    assert "отчет" in result
    assert "для" not in result


def test_retriever_ranks_relevant_topic_first():
    need = make_need()
    relevant = make_topic(
        "topic-report",
        "Выгрузка отчета по скважине и подготовка отчетности",
    )
    unrelated = make_topic(
        "topic-map",
        "Отображение карты месторождения и координат скважин",
    )

    result = PassportRetriever(k=10, p_max=50).retrieve(
        need,
        make_snapshot(unrelated, relevant),
    )

    assert result.candidates[0].topic.topic_id == "topic-report"
    assert result.total_topic_count == 2
    assert result.candidate_set_complete is True


def test_retriever_marks_truncated_catalog_incomplete():
    topics = [
        make_topic(f"topic-{index}", f"Описание темы номер {index}")
        for index in range(5)
    ]

    result = PassportRetriever(k=2, p_max=50).retrieve(
        make_need(),
        make_snapshot(*topics),
    )

    assert len(result.candidates) == 2
    assert result.searched_topic_count == 2
    assert result.candidate_set_complete is False


def test_retriever_handles_empty_catalog():
    result = PassportRetriever().retrieve(
        make_need(),
        make_snapshot(),
    )

    assert result.candidates == ()
    assert result.total_topic_count == 0
    assert result.searched_topic_count == 0
    assert result.candidate_set_complete is True


def test_batching_respects_character_limit():
    topics = [
        make_topic("topic-1", "Короткий паспорт"),
        make_topic("topic-2", "Еще один короткий паспорт"),
    ]
    candidates = [
        SimpleNamespace(topic=topic, score=0.5, matched_terms=())
        for topic in topics
    ]

    plan = build_batches(
        candidates,
        max_chars=1000,
        max_batches=8,
    )

    assert plan.complete is True
    assert plan.included_topics == 2
    assert sum(len(batch.topics) for batch in plan.batches) == 2


def test_batching_marks_limit_truncation():
    topics = [
        make_topic(f"topic-{index}", f"Описание темы {index}")
        for index in range(4)
    ]
    candidates = [
        SimpleNamespace(topic=topic, score=0.5, matched_terms=())
        for topic in topics
    ]

    plan = build_batches(
        candidates,
        max_chars=250,
        max_batches=1,
    )

    assert plan.complete is False
    assert plan.included_topics < plan.total_topics


def test_classification_metrics():
    result = classification_metrics(
        ["match", "review", "new"],
        ["match", "review", "review"],
    )

    assert result.total == 3
    assert result.correct == 2
    assert result.accuracy == pytest.approx(2 / 3)
    assert result.coverage == 1.0


def test_extraction_metrics():
    result = extraction_metrics(
        ["Выгрузка отчета", "Создание заявки"],
        [" выгрузка   отчета ", "Лишняя потребность"],
    )

    assert result.expected_count == 2
    assert result.actual_count == 2
    assert result.matched_count == 1
    assert result.precision == pytest.approx(0.5)
    assert result.recall == pytest.approx(0.5)
    assert result.f1 == pytest.approx(0.5)
