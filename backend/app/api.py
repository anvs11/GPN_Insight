from datetime import date
from typing import Literal

from fastapi import APIRouter, Request

from .db import connection
from .gateways import GatewayUnavailable, model_health
from .repositories import RepositoryError
from .public_views import public_source, public_topic
from .schemas import DecisionUpdate, ErrorResponse, Health, ModelAvailability, ReviewItem, Run, RunRequest, SourceDetail, TopicDetail, TopicStatus

router = APIRouter(prefix="/api/v1", responses={
    code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 501, 503)
})


@router.get("/health", response_model=Health)
def health(request: Request) -> Health:
    with connection(request.app.state.repository.path) as db:
        db.execute("SELECT version FROM schema_version").fetchone()
    result = model_health(request.app.state.settings)
    service = request.app.state.run_service
    result.processing_state = "busy" if service.repo.active_run() else "idle"
    result.pipeline_ready = service.adapters is not None
    result.integration_errors = [] if result.pipeline_ready else ["NORMALIZER_NOT_CONNECTED", "ANALYSIS_NOT_CONNECTED"]
    binding_error = getattr(request.app.state, "text_pipeline_error", None)
    if not result.pipeline_ready and binding_error:
        result.integration_errors.append(binding_error)
    storage = getattr(request.app.state, "import_storage_report", None)
    if storage:
        result.integration_errors.extend(storage.issues)
    if service.adapters is not None and service.adapters.partial:
        try:
            bundle = service.adapters.normalizer.asr.records()
            for kind, operation in (("asr", "transcribe"), ("vision", "describe")):
                known = [r for r in bundle.records.values() if r.request.operation == operation]
                result.available_models[kind] = ModelAvailability(status="available" if known else "unavailable",
                    configured=True, code="MEDIA_REPLAY_READY" if known else "REPLAY_RECORD_MISSING",
                    message=f"Replay: проверено записей {len(known)}. Только точные известные входы; live не подключён.")
            result.replay_origins = sorted(set(result.replay_origins) | {r.metadata.origin for r in bundle.records.values()})
            result.replay_record_count += len(bundle.records)
        except GatewayUnavailable as exc:
            for kind in ("asr", "vision"):
                result.available_models[kind] = ModelAvailability(status="unavailable", configured=True,
                    code=exc.code, message="Media replay недоступен; live не подставляется. Проверьте точный набор.")
            result.integration_errors.append(exc.code)
    return result


@router.post("/runs", response_model=Run, status_code=202)
def start_run(body: RunRequest, request: Request) -> Run:
    return request.app.state.run_service.start(body)


@router.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str, request: Request) -> Run:
    return request.app.state.repository.get_run(run_id)


@router.get("/topics", response_model=list[TopicDetail])
def topics(request: Request, support_level: Literal["single", "repeated", "all"] = "repeated",
           product_id: Literal["webneft"] = "webneft", status: TopicStatus | None = None,
           date_from: date | None = None, date_to: date | None = None,
           review_due: Literal["true", "false"] | None = None) -> list[TopicDetail]:
    _filters(request, {"support_level", "product_id", "status", "date_from", "date_to", "review_due"})
    repo = request.app.state.repository
    return [public_topic(card) for card in repo.list_topics(request.app.state.settings.app_model_mode, product_id=product_id,
        support_level=support_level, status=status, review_due=None if review_due is None else review_due == "true",
        date_from=date_from, date_to=date_to)]


def _filters(request: Request, allowed: set[str]) -> None:
    if any(name not in allowed or len(request.query_params.getlist(name)) != 1 for name in request.query_params):
        raise RepositoryError("UNSUPPORTED_FILTER", "Уберите неизвестные или повторные параметры фильтра.", 422)


@router.get("/topics/{topic_id}", response_model=TopicDetail)
def topic(topic_id: str, request: Request, date_from: date | None = None, date_to: date | None = None) -> TopicDetail:
    _filters(request, {"date_from", "date_to"})
    return public_topic(request.app.state.repository.get_topic(topic_id, request.app.state.settings.app_model_mode,
        date_from=date_from, date_to=date_to))


@router.get("/sources/{source_id}", response_model=SourceDetail)
def source(source_id: str, request: Request) -> SourceDetail:
    return public_source(request.app.state.repository.get_source(source_id))


@router.get("/reviews", response_model=list[ReviewItem])
@router.get("/review-items", response_model=list[ReviewItem])
def reviews(request: Request, status: Literal["open", "resolved", "insufficient_data", "all"] = "open") -> list[ReviewItem]:
    values = list(request.query_params.multi_items())
    if len(values) > 1 or any(name != "status" for name, _ in values):
        raise RepositoryError("UNSUPPORTED_FILTER", "Допустим один status: open, resolved, insufficient_data или all.", 422)
    return request.app.state.repository.list_reviews(None if status == "all" else status)


@router.get("/reviews/{review_id}", response_model=ReviewItem)
@router.get("/review-items/{review_id}", response_model=ReviewItem)
def review(review_id: str, request: Request) -> ReviewItem:
    return request.app.state.repository.get_review(review_id)


@router.patch("/topics/{topic_id}/decision", response_model=TopicDetail)
def decision(topic_id: str, update: DecisionUpdate, request: Request) -> TopicDetail:
    from .workflow_api import no_query, validate_issue_ref
    no_query(request)
    validate_issue_ref(update.existing_issue_ref, request.app.state.settings.app_issue_hosts)
    repo = request.app.state.repository
    # Ответ содержит именно сохранённую версию даже при параллельном PATCH.
    with repo.transaction() as tx:
        tx.record_decision(topic_id, update)
        return public_topic(tx.get_topic(topic_id, request.app.state.settings.app_model_mode))
