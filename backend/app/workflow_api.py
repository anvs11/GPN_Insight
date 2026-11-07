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
        return tx.get_topic(topic_id, request.app.state.settings.app_model_mode)


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
    return HumanWorkflow(request.app.state.repository).merge_preview(topic_id, body.target_topic_id,
        request.app.state.settings.app_model_mode)


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
