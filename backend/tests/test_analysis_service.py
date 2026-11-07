from types import SimpleNamespace

import pytest

from app.analysis.service import AnalysisService
from app.config import Settings
from app.schemas import (
    BatchAssignment,
    CatalogSnapshot,
    EvidenceRef,
    ExtractedNeed,
    PassportExample,
    ProcessingProfile,
    RecordingMeta,
    Topic,
    TopicPassport,
)


def make_need():
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


def make_topic(topic_id, text):
    seed_ref = EvidenceRef(
        source_id="seed-source",
        fragment_id="seed-fragment",
        quote=text,
    )
    passport = TopicPassport(
        scenario="Подготовка отчетности",
        need_statement=text,
        scope="Выгрузка и формирование отчетов",
        inclusion_examples=[
            PassportExample(text=text, evidence_refs=[seed_ref])
        ],
        version=1,
    )
    return Topic(
        topic_id=topic_id,
        passport=passport,
        title=f"Тема {topic_id}",
        summary=text,
        need_ids=[],
        evidence=[seed_ref],
    )


def make_snapshot(*topics):
    return CatalogSnapshot(
        product_id="webneft",
        catalog_revision=1,
        topics=list(topics),
    )


def make_evidence(need=None, *, quote=None):
    need = need or make_need()
    return EvidenceRef(
        source_id=need.source_id,
        fragment_id=need.fragment_id,
        quote=quote if quote is not None else need.evidence_quote,
    )


def make_batch_response(
    need=None,
    *,
    decision="review",
    matched_topic_id=None,
    candidate_topic_ids=None,
    evidence_refs=None,
):
    need = need or make_need()
    return BatchAssignment(
        decision=decision,
        matched_topic_id=matched_topic_id,
        candidate_topic_ids=candidate_topic_ids or [],
        reason="Синтетический ответ тестовой модели",
        evidence_refs=evidence_refs or [make_evidence(need)],
    )


class FakeGateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, request, response_schema):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Неожиданный дополнительный вызов gateway")
        value = self.responses.pop(0)
        assert isinstance(value, response_schema)
        metadata = RecordingMeta(
            request_hash="a" * 64,
            prompt_hash="a" * 64,
            normalization_hash="b" * 64,
            model_id="synthetic-test-only",
            origin="authored",
            recording_set_version="test-1",
        )
        return SimpleNamespace(value=value, metadata=metadata)


def make_service(gateway, *, retrieval_k=20, max_chars=16000, max_batches=8):
    profile = ProcessingProfile(
        model_id="synthetic-test-only",
        normalization_version="test-1",
        prompt_hashes={
            "extract_needs": "a" * 64,
            "assign_topic": "a" * 64,
            "propose_topic": "c" * 64,
        },
        recording_set_version="test-1",
    )
    settings = Settings(
        _env_file=None,
        app_retrieval_k=retrieval_k,
        app_p_max=50,
        app_passport_batch_max_chars=max_chars,
        app_max_assign_batches=max_batches,
    )
    return AnalysisService(
        gateway=gateway,
        profile=profile,
        settings=settings,
        mode="replay",
    )


def test_analysis_service_returns_match_for_complete_unambiguous_result():
    need = make_need()
    topic = make_topic(
        "topic-report",
        "Выгрузка отчета по скважине и подготовка отчетности",
    )
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="match",
            matched_topic_id="topic-report",
            candidate_topic_ids=["topic-report"],
        )
    )
    service = make_service(gateway)

    result = service.assign_topic(need, make_snapshot(topic))

    assert result.assignment.decision == "match"
    assert result.assignment.matched_topic_id == "topic-report"
    assert result.assignment.candidate_set_complete is True
    assert result.assignment.search_truncated is False
    assert result.assignment.passport_versions == {"topic-report": 1}
    assert len(result.metadata) == 1
    assert len(gateway.requests) == 1


def test_analysis_service_marks_multiple_candidates_as_review():
    need = make_need()
    first = make_topic(
        "topic-report-1",
        "Выгрузка отчета по скважине",
    )
    second = make_topic(
        "topic-report-2",
        "Подготовка отчетности и формирование отчета по скважине",
    )
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="match",
            matched_topic_id="topic-report-1",
            candidate_topic_ids=["topic-report-1", "topic-report-2"],
        )
    )
    service = make_service(gateway)

    result = service.assign_topic(need, make_snapshot(first, second))

    assert result.assignment.decision == "review"
    assert result.assignment.matched_topic_id is None
    assert set(result.assignment.candidate_topic_ids) == {
        "topic-report-1",
        "topic-report-2",
    }


def test_analysis_service_preserves_model_review_decision():
    need = make_need()
    topic = make_topic(
        "topic-report",
        "Выгрузка отчета по скважине",
    )
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="review",
            candidate_topic_ids=["topic-report"],
        )
    )
    service = make_service(gateway)

    result = service.assign_topic(need, make_snapshot(topic))

    assert result.assignment.decision == "review"
    assert result.assignment.matched_topic_id is None


def test_analysis_service_searches_all_topics_when_retrieval_k_is_one():
    need = make_need()
    relevant = make_topic(
        "topic-report",
        "???????? ?????? ?? ????????",
    )
    unrelated = make_topic(
        "topic-map",
        "??????????? ????? ????????????? ? ?????????",
    )
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="match",
            matched_topic_id="topic-report",
            candidate_topic_ids=["topic-report"],
        )
    )
    service = make_service(gateway, retrieval_k=1)

    result = service.assign_topic(
        need,
        make_snapshot(relevant, unrelated),
    )

    assert result.assignment.decision == "match"
    assert result.assignment.matched_topic_id == "topic-report"
    assert result.assignment.searched_topic_count == 2
    assert result.assignment.total_topic_count == 2
    assert result.assignment.candidate_set_complete is True
    assert result.assignment.search_truncated is False


def test_analysis_service_handles_empty_catalog():
    need = make_need()
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="provisional_no_match",
            candidate_topic_ids=[],
        )
    )
    service = make_service(gateway)

    result = service.assign_topic(need, make_snapshot())

    assert result.assignment.decision == "review"
    assert result.assignment.candidate_set_complete is True
    assert result.assignment.search_truncated is False
    assert result.assignment.total_topic_count == 0
    assert result.assignment.searched_topic_count == 0
    assert result.assignment.passport_versions == {}
    assert len(gateway.requests) == 1
    assert gateway.requests[0].data["batch_count"] == 1


def test_analysis_service_rejects_unknown_candidate_ids():
    need = make_need()
    topic = make_topic(
        "topic-report",
        "Выгрузка отчета по скважине",
    )
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="review",
            candidate_topic_ids=["topic-does-not-exist"],
        )
    )
    service = make_service(gateway)

    with pytest.raises(ValueError, match="неизвестные topic_id"):
        service.assign_topic(need, make_snapshot(topic))


def test_analysis_service_rejects_fabricated_evidence():
    need = make_need()
    topic = make_topic(
        "topic-report",
        "Выгрузка отчета по скважине",
    )
    gateway = FakeGateway(
        make_batch_response(
            need,
            decision="match",
            matched_topic_id="topic-report",
            candidate_topic_ids=["topic-report"],
            evidence_refs=[
                make_evidence(need, quote="Выдуманная цитата модели")
            ],
        )
    )
    service = make_service(gateway)

    with pytest.raises(ValueError, match="evidence_refs"):
        service.assign_topic(need, make_snapshot(topic))