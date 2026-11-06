from typing import Literal

from fastapi import APIRouter, Request

from .db import connection
from .gateways import model_health
from .repositories import RepositoryError
from .schemas import DecisionUpdate, ErrorResponse, Health, Run, RunRequest, SourceDetail, TopicDetail

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
    return result


@router.post("/runs", response_model=Run, status_code=202)
def start_run(body: RunRequest, request: Request) -> Run:
    return request.app.state.run_service.start(body)


@router.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str, request: Request) -> Run:
    return request.app.state.repository.get_run(run_id)


@router.get("/topics", response_model=list[TopicDetail])
def topics(request: Request, support_level: Literal["single", "repeated"] | None = "repeated") -> list[TopicDetail]:
    if set(request.query_params) - {"support_level"}:
        raise RepositoryError("UNSUPPORTED_FILTER", "На этапе 1 список поддерживает только фильтр support_level. Уберите остальные параметры.", 422)
    repo = request.app.state.repository
    return [repo.get_topic(t, request.app.state.settings.app_model_mode) for t in repo.list_topic_ids(support_level)]


@router.get("/topics/{topic_id}", response_model=TopicDetail)
def topic(topic_id: str, request: Request) -> TopicDetail:
    return request.app.state.repository.get_topic(topic_id, request.app.state.settings.app_model_mode)


@router.get("/sources/{source_id}", response_model=SourceDetail)
def source(source_id: str, request: Request) -> SourceDetail:
    return request.app.state.repository.get_source(source_id)


@router.patch("/topics/{topic_id}/decision", response_model=TopicDetail)
def decision(topic_id: str, update: DecisionUpdate, request: Request) -> TopicDetail:
    if update.status not in {"new", "needs_research"}:
        raise RepositoryError("FEATURE_NOT_IMPLEMENTED", "Сейчас доступны статусы «Новое» и «Исследуется». Для остальных решений требуется следующая версия сервиса.", 501)
    repo = request.app.state.repository
    # Ответ содержит именно сохранённую версию даже при параллельном PATCH.
    with repo.transaction() as tx:
        tx.record_decision(topic_id, update)
        return tx.get_topic(topic_id, request.app.state.settings.app_model_mode)
