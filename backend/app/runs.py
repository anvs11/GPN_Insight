"""Один worker на приложение; mode задаёт сервер, активный клик идемпотентен."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event, RLock
from uuid import uuid4

from .coordination import gate_for
from .errors import RepositoryError
from .pipeline import Pipeline, PipelineAdapters, RunStopped
from .schemas import Run, RunRequest
from .storage import now


class RunService:
    def __init__(self, repository, mode, adapters: PipelineAdapters | None):
        self.repo = repository
        self.mode = mode
        self.adapters = adapters
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpn-pipeline")
        self.stop = Event()
        self.lock = RLock()
        self.futures = {}

    def start(self, request: RunRequest) -> Run:
        request = RunRequest.model_validate(request.model_dump())
        with self.lock, gate_for(self.repo.path).start_run():
            if self.stop.is_set():
                raise RepositoryError("WORKER_STOPPING", "Сервер останавливается. Повторите после запуска.", 503)
            active = self.repo.active_run()
            if active:
                return active
            if self.adapters is None:
                raise RepositoryError("INTEGRATION_NOT_READY", "Нормализация участника 1 и анализ участника 2 ещё не подключены. Запуск не создан.", 503)
            with self.repo.transaction() as tx:
                sources = tx.select_run_sources(request)
                run = tx._create_run(Run(run_id=str(uuid4()), product_id=request.product_id, mode=self.mode,
                    state="queued", stage="queued", created_at=now(), total_materials=sum(
                        len(s.ticket.assets) + (int(bool(s.ticket.text) or not s.ticket.assets) if self.adapters.partial else 1) for s in sources),
                    coverage=0.0 if sources else None))
                tx.save_run_inputs(run.run_id, request, self.adapters.profile, sources)
            try:
                self.futures[run.run_id] = self.executor.submit(copy_context().run, self._execute, run.run_id)
            except RuntimeError:
                self.repo.save_run(run.model_copy(update={"state": "failed", "stage": "worker", "finished_at": now(),
                    "failure_code": "WORKER_UNAVAILABLE", "failure_message": "Worker недоступен. Повторите запуск позже."}))
                raise RepositoryError("WORKER_UNAVAILABLE", "Worker недоступен. Повторите запуск позже.", 503)
            return run

    def _execute(self, run_id):
        try:
            Pipeline(self.repo, self.adapters, self.stop).execute(run_id)
        except RunStopped:
            pass
        except Exception as exc:
            with self.repo.transaction() as tx:
                current = tx.get_run(run_id)
                if current.state not in {"queued", "running"}:
                    return
                tx.save_run(Run.model_validate({**current.model_dump(), "state": "failed", "stage": "failed", "finished_at": now(),
                    "failure_code": exc.code if isinstance(exc, RepositoryError) else "PIPELINE_FAILED",
                    "failure_message": "Запуск не завершён. Проверьте интеграцию и повторите; успешные этапы сохранены."}))

    def close(self):
        with self.lock:
            self.stop.set()
            self.repo.interrupt_active_runs()
            self.executor.shutdown(wait=False, cancel_futures=True)
