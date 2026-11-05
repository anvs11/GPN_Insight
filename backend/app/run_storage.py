"""Снимки входов и успешные контрольные точки pipeline, без правил анализа."""

import json
from zoneinfo import ZoneInfo

from .errors import RepositoryError
from .schemas import ProcessingProfile, Run, RunRequest


class RunStorage:
    def active_run(self) -> Run | None:
        with self._session() as db:
            row = db.execute("SELECT body_json FROM run_records WHERE state IN ('queued','running')").fetchone()
            return Run.model_validate_json(row[0]) if row else None

    def select_run_sources(self, request: RunRequest):
        with self._session() as db:
            ids = [r[0] for r in db.execute("SELECT source_id FROM source_records WHERE product_id=?", (request.product_id,))]
            sources = [self.get_source(source_id, request.product_id) for source_id in ids]
        return sorted((s for s in sources if
                       (request.date_from is None or s.ticket.created_at.astimezone(ZoneInfo('Europe/Moscow')).date() >= request.date_from) and
                       (request.date_to is None or s.ticket.created_at.astimezone(ZoneInfo('Europe/Moscow')).date() <= request.date_to)),
                      key=lambda s: (s.ticket.created_at, s.ticket.source_id))

    def save_run_inputs(self, run_id: str, request: RunRequest, profile: ProcessingProfile, sources) -> None:
        body = {"request": request.model_dump(mode="json"), "profile": profile.model_dump(mode="json"),
                "sources": [s.model_dump(mode="json") for s in sources]}
        with self._session(write=True) as db:
            db.execute("INSERT INTO run_inputs VALUES (?,?)", (run_id, json.dumps(body, ensure_ascii=False)))

    def get_run_inputs(self, run_id: str) -> dict:
        with self._session() as db:
            row = db.execute("SELECT body_json FROM run_inputs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise RepositoryError("RUN_INPUTS_MISSING", "Входы запуска не сохранены. Создайте новый запуск.", 404)
            return json.loads(row[0])

    def checkpoint(self, key: str) -> dict | None:
        with self._session() as db:
            row = db.execute("SELECT body_json FROM stage_checkpoints WHERE checkpoint_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_checkpoint(self, key: str, stage: str, run_id: str, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._session(write=True) as db:
            existing = db.execute("SELECT body_json FROM stage_checkpoints WHERE checkpoint_key=?", (key,)).fetchone()
            if existing:
                if existing[0] != body:
                    raise RepositoryError("CHECKPOINT_CONFLICT", "Успешный этап уже сохранён с другим результатом.")
                return
            db.execute("INSERT INTO stage_checkpoints VALUES (?,?,?,?)", (key, stage, run_id, body))
