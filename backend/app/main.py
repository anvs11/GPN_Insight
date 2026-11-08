import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import router
from .bootstrap import configured_text_pipeline
from .config import Settings
from .db import initialize
from .gateways import UnavailableGateways, text_gateway
from .imports_api import router as imports_router
from .ingestion.media_files import reconcile_import_storage, save_recovery_report
from .pipeline import PipelineAdapters
from .repositories import Repository, RepositoryError
from .runs import RunService
from .workflow_api import router as workflow_router


def create_app(settings: Settings | None = None, *, pipeline_adapters: PipelineAdapters | None = None) -> FastAPI:
    # Режим/профиль процесса фиксируются вместе: изменение объекта вызывающим
    # кодом не должно переименовать работающий replay в live в health.
    settings = (settings or Settings()).model_copy(deep=True)
    repository = Repository(settings.repo_path(settings.app_db_path))
    configured_gateway = text_gateway(settings)
    binding_error = None
    if pipeline_adapters is None:
        pipeline_adapters, binding_error = configured_text_pipeline(settings, configured_gateway)
    runs = RunService(repository, settings.app_model_mode, pipeline_adapters)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize(repository.path)
        repository.interrupt_active_runs()
        app.state.import_storage_report = reconcile_import_storage(repository, settings)
        try:
            save_recovery_report(app.state.import_storage_report)
        except (OSError, RepositoryError):
            app.state.import_storage_report.issues.append("IMPORT_RECOVERY_REPORT_UNAVAILABLE")
        try:
            yield
        finally:
            runs.close()

    # Swagger/ReDoc по умолчанию загружают JS с CDN. Оставляем локальный OpenAPI JSON.
    app = FastAPI(title="GPN Insight — основа backend", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.repository = repository
    app.state.gateways = UnavailableGateways(settings)
    app.state.text_gateway = configured_gateway
    app.state.text_pipeline_error = binding_error
    app.state.run_service = runs
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        forbidden_origin = False
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if origin and origin not in {"http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"}:
                forbidden_origin = True
        if forbidden_origin:
            response = JSONResponse(status_code=403, content={"code": "ORIGIN_FORBIDDEN", "message": "Запрос с посторонней страницы запрещён. Откройте локальный интерфейс."})
        else:
            response = await call_next(request)
            if response.status_code == 400:
                response = JSONResponse(status_code=400, content={"code": "BAD_REQUEST", "message": "Запрос отклонён. Проверьте адрес локального сервера и формат запроса."})
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        # Windows PowerShell 5.1 иначе декодирует JSON как однобайтный текст.
        if response.headers.get("Content-Type") == "application/json":
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(RepositoryError)
    async def repository_error(request: Request, exc: RepositoryError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI по умолчанию возвращает input, который может содержать обращение/секрет.
        return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "Данные не соответствуют контракту. Проверьте обязательные поля, значения и версию."})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"code": "HTTP_ERROR", "message": "Запрошенная операция недоступна. Проверьте адрес и метод."})

    @app.exception_handler(sqlite3.Error)
    async def database_error(request: Request, exc: sqlite3.Error):
        return JSONResponse(status_code=503, content={"code": "DATABASE_UNAVAILABLE", "message": "База временно недоступна. Повторите запрос; сохранение не подтверждено."})

    app.include_router(router)
    app.include_router(imports_router)
    app.include_router(workflow_router)
    return app


app = create_app()
