"""HTTP ручного рассмотрения. Автоматические модельные политики сюда не входят."""

from urllib.parse import urlsplit
from datetime import date
from typing import Literal
import re

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from .errors import RepositoryError
from .schemas import (
    ErrorResponse, ExcludeNeedsRequest, ExcludeResult, MergePreview, MergePreviewRequest,
    MergeResult, MergeTopicsRequest, PassportUpdate, ResolveReviewRequest, ReviewItem, RunRequest,
    TopicChangeEvent, TopicDetail, TopicPassport,
)
from .workflow import HumanWorkflow
from .public_views import public_topic
from .processing_schemas import ProcessingDetail
from .assessment_schemas import TopicAssessmentSummary
from .weekly import CompletedWeek

router = APIRouter(prefix="/api/v1", tags=["manual-workflow"], responses={
    code: {"model": ErrorResponse} for code in (404, 409, 422, 501, 503)})


def no_query(request: Request) -> None:
    if request.query_params:
        raise RepositoryError("UNSUPPORTED_FILTER", "Операция не принимает query-параметры. Передайте согласованное JSON-тело.", 422)


@router.patch("/topics/{topic_id}/passport", response_model=TopicDetail)
def update_passport(topic_id: str, body: PassportUpdate, request: Request) -> TopicDetail:
    no_query(request)
    repo = request.app.state.repository
    with repo.transaction() as tx:
        current = tx.get_topic(topic_id, request.app.state.settings.app_model_mode).topic
        if current.passport.version != body.passport_version:
            raise RepositoryError("VERSION_CONFLICT", "Паспорт изменён. Перечитайте карточку перед сохранением.")
        passport = TopicPassport(**body.passport.model_dump(), product_id=current.passport.product_id,
                                 version=current.passport.version + 1)
        tx.update_passport(topic_id, passport, expected_version=body.version, author=body.author,
                           reason=body.reason, title=body.title)
        return public_topic(tx.get_topic(topic_id, request.app.state.settings.app_model_mode))


def validate_issue_ref(value: str | None, allowed_hosts: str) -> None:
    if value is None:
        return
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value):
        return
    try:
        url = urlsplit(value)
        hosts = {host.strip().lower().rstrip(".") for host in allowed_hosts.split(",") if host.strip()}
        valid = (url.scheme == "https" and url.hostname and url.hostname.lower().rstrip(".") in hosts
                 and url.port in {None, 443} and url.username is None and url.password is None
                 and not any(ord(c) <= 32 or ord(c) == 127 for c in value) and "\\" not in value)
    except ValueError:
        valid = False
    if not valid:
        raise RepositoryError("ISSUE_REF_INVALID", "Укажите ID задачи либо HTTPS-ссылку разрешённого внутреннего хоста APP_ISSUE_HOSTS.", 422)


@router.post("/review-items/{review_id}/resolve", response_model=ReviewItem)
@router.post("/reviews/{review_id}/resolve", response_model=ReviewItem)
def resolve_review(review_id: str, body: ResolveReviewRequest, request: Request) -> ReviewItem:
    no_query(request)
    return HumanWorkflow(request.app.state.repository).resolve_review(review_id, body)


@router.post("/topics/{topic_id}/merge-preview", response_model=MergePreview)
def merge_preview(topic_id: str, body: MergePreviewRequest, request: Request) -> MergePreview:
    no_query(request)
    preview = HumanWorkflow(request.app.state.repository).merge_preview(topic_id, body.target_topic_id,
        request.app.state.settings.app_model_mode)
    return MergePreview(source=public_topic(preview.source), target=public_topic(preview.target), decision_conflict=preview.decision_conflict)


@router.post("/topics/{topic_id}/merge", response_model=MergeResult)
def merge_topics(topic_id: str, body: MergeTopicsRequest, request: Request) -> MergeResult:
    no_query(request)
    return HumanWorkflow(request.app.state.repository).merge_topics(topic_id, body, request.app.state.settings.app_model_mode)


@router.post("/topics/{topic_id}/exclude", response_model=ExcludeResult)
def exclude_needs(topic_id: str, body: ExcludeNeedsRequest, request: Request) -> ExcludeResult:
    no_query(request)
    return HumanWorkflow(request.app.state.repository).exclude_needs(topic_id, body)


@router.get("/topics/{topic_id}/changes", response_model=list[TopicChangeEvent])
def topic_changes(topic_id: str, request: Request) -> list[TopicChangeEvent]:
    no_query(request)
    repo = request.app.state.repository
    with repo.transaction() as tx:
        tx.get_topic(topic_id, request.app.state.settings.app_model_mode)
        return tx.topic_changes(topic_id)


@router.get("/review-items/{review_id}/history", response_model=list[ReviewItem])
@router.get("/reviews/{review_id}/history", response_model=list[ReviewItem])
def review_history(review_id: str, request: Request) -> list[ReviewItem]:
    no_query(request)
    repo = request.app.state.repository
    with repo.transaction() as tx:
        tx.get_review(review_id)
        return tx.review_history(review_id)


@router.get("/report", response_class=PlainTextResponse)
@router.get("/reports", response_class=PlainTextResponse)
def report(request: Request, product_id: Literal["webneft"] = "webneft",
           date_from: date | None = None, date_to: date | None = None):
    if any(name not in {"product_id", "date_from", "date_to"} or len(request.query_params.getlist(name)) != 1
           for name in request.query_params):
        raise RepositoryError("UNSUPPORTED_FILTER", "Допустимы однократные product_id, date_from и date_to.", 422)
    if date_from and date_to and date_from > date_to:
        raise RepositoryError("INVALID_PERIOD", "Начало периода позже окончания.", 422)
    from .report import build_report
    text = build_report(request.app.state.repository, request.app.state.settings.app_model_mode,
        RunRequest(product_id=product_id, date_from=date_from, date_to=date_to))
    return PlainTextResponse(text, media_type="text/markdown")


@router.get("/reports/weekly", response_class=PlainTextResponse)
def weekly_report(request: Request):
    no_query(request)
    from .weekly import build_weekly_report
    return PlainTextResponse(build_weekly_report(request.app.state.repository, request.app.state.settings.app_model_mode),
        media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="gpn-insight-weekly.md"'})


@router.get("/reports/weekly/period", response_model=CompletedWeek)
def weekly_period(request: Request):
    no_query(request)
    from .weekly import completed_week
    return completed_week()


@router.get("/sources/{source_id}/processing", response_model=ProcessingDetail)
def processing_result(source_id: str, request: Request, source_version: int | None = None, revision_id: str | None = None):
    if any(k not in {"source_version", "revision_id"} or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise RepositoryError("UNSUPPORTED_FILTER", "Допустимы source_version и revision_id.", 422)
    repo = request.app.state.repository
    with repo.transaction() as tx:
        source = tx.get_source(source_id, version=source_version)
        result = tx.processing_result(source_id, version=source_version, revision_id=revision_id)
        if result is None:
            raise RepositoryError("PROCESSING_RESULT_NOT_FOUND", "Для этой версии нет partial результата. Старый текстовый результат доступен в источнике.", 404)
        need_ids = [r[0] for r in tx._db.execute("SELECT need_id FROM processing_needs WHERE revision_id=? ORDER BY need_id", (result.revision_id,))]
        assessments = {nid: tx.assessment(nid, result.analysis_digest) for nid in need_ids}
        # Полный envelope содержит только masked normalized text, не originals.
        return {"format_version": "partial-source-v1", "result": result,
                "manual_recheck_required": source.manual_recheck_required,
                "current_head_revision": source.current_result_revision, "is_current": source.current_result_revision == result.revision_id,
                "analysis_pending": source.analysis_pending if source.current_result_revision == result.revision_id else None,
                "assessments": assessments, "unassessed_need_ids": [n for n, v in assessments.items() if v is None]}


@router.get("/topics/{topic_id}/assessment-summary", response_model=TopicAssessmentSummary)
def topic_assessments(topic_id: str, request: Request):
    no_query(request)
    with request.app.state.repository.transaction() as tx:
        card = tx.get_topic(topic_id, request.app.state.settings.app_model_mode)
        categories, priorities, missing = {}, {}, set()
        for source in card.sources:
            result = tx.processing_result(source.ticket.source_id) if tx._has_processing() else None
            for need in source.needs:
                if need.need_id not in card.topic.need_ids:
                    continue
                assessment = tx.assessment(need.need_id, result.analysis_digest) if result and not source.analysis_pending else None
                if assessment is None:
                    missing.add(source.ticket.source_id)
                    continue
                categories.setdefault(assessment.assessment.category, set()).add(source.ticket.source_id)
                priorities.setdefault(assessment.assessment.priority, set()).add(source.ticket.source_id)
        return {"format_version": "topic-assessment-summary-v1", "topic_id": topic_id, "topic_version": card.topic.version,
            "unit": "sources", "source_count": len(card.sources), "unassessed_source_count": len(missing),
            "categories": {k: len(v) for k, v in categories.items()}, "priorities": {k: len(v) for k, v in priorities.items()},
            "aggregation": "distinct-source-per-value; categories/priorities overlap; no inferred normal priority"}
