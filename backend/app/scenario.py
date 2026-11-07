"""Ограниченный текстовый сценарий через обычные сервисы, на новой SQLite."""

import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .clock import fixed_clock
from .config import Settings
from .db import initialize
from .gateways import GatewayUnavailable
from .ingestion.importer import import_tickets
from .ingestion.integration import build_text_pipeline, build_text_profile
from .replay import _contained, _read_json, canonical, semantic_hash
from .repositories import Repository, RepositoryError
from .runs import RunService
from .schemas import (
    Contract, DecisionUpdate, ExcludeNeedsRequest, Identifier, MergeTopicsRequest,
    ResolveReviewRequest, RunRequest, Sha256, Text, TicketInput,
)
from .workflow import HumanWorkflow

FLOW_IDS = ("FLOW-001", "FLOW-002", "FLOW-003", "FLOW-004")
FLOW_ACTIONS = ["base", "decision", "passport", "repeat", "delta", "review", "exclude", "merge", "restart"]
INITIAL_STATE = {"format": 1, "synthetic": True, "schema_version": 3, "state": "empty", "product_id": "webneft"}


class AuthoredNeed(Contract):
    label: Literal["export", "filters", "review"]
    quote: Text
    statement: Text
    scenario: Text


class ScenarioSpec(Contract):
    format: Literal[1]
    scenario_id: Identifier
    synthetic: Literal[True]
    clock: AwareDatetime
    initial_state: Literal["initial_state.json"]
    initial_state_hash: Sha256
    recording_set_version: Text
    model_id: Text
    actions: list[str]
    base: Annotated[list[TicketInput], Field(min_length=3, max_length=3)]
    delta: TicketInput
    authored_needs: dict[str, list[AuthoredNeed]]

    @model_validator(mode="after")
    def bounded_text_flow(self):
        tickets = [*self.base, self.delta]
        if [t.source_id for t in tickets] != list(FLOW_IDS) or self.actions != FLOW_ACTIONS:
            raise ValueError("Unsupported scenario v1 action/source sequence")
        labels = [[n.label for n in self.authored_needs.get(t.source_id, [])] for t in tickets]
        if labels != [["export", "filters"], ["export"], ["review"], ["export"]] or set(self.authored_needs) != set(FLOW_IDS):
            raise ValueError("Unsupported authored fixture layout")
        for ticket in tickets:
            if ticket.product_id != "webneft" or ticket.assets or not ticket.text or len(ticket.text) > 16000 or ticket.created_at > self.clock:
                raise ValueError("Only bounded synthetic text before scenario clock is supported")
            if any(n.quote not in ticket.text for n in self.authored_needs[ticket.source_id]):
                raise ValueError("Authored quote must belong to the source")
        if [t.created_at for t in tickets] != sorted(t.created_at for t in tickets):
            raise ValueError("Stable source order is required")
        return self


def require(condition: bool, code="SCENARIO_ASSERTION_FAILED") -> None:
    if not condition:
        raise GatewayUnavailable(code, "Сценарная проверка не подтверждена. Проверьте код ошибки и отдельный отчёт; рабочая база не используется.")


def load_scenario(path: Path) -> tuple[ScenarioSpec, dict]:
    spec = ScenarioSpec.model_validate(_read_json(path, 512 * 1024))
    snapshot = _read_json(_contained(path.parent, spec.initial_state), 4096)
    require(snapshot == INITIAL_STATE, "SCENARIO_INITIAL_STATE_UNSUPPORTED")
    require(semantic_hash(snapshot) == spec.initial_state_hash, "SCENARIO_INITIAL_STATE_MISMATCH")
    return spec, snapshot


def scenario_profile(spec: ScenarioSpec, database: Path):
    settings = Settings(_env_file=None, app_db_path=database, app_model_mode="replay")
    return settings, build_text_profile(settings, model_id=spec.model_id,
        recording_set_version=spec.recording_set_version, parameters={"temperature": 0})


def semantic_view(value):
    """Audit UUID и явный transport-mode не определяют смысл record/replay."""
    if isinstance(value, dict):
        return {key: semantic_view(item) for key, item in value.items()
                if key not in {"event_id", "override_id", "registration_id", "run_id", "model_mode", "analysis_mode"}}
    if isinstance(value, list):
        return [semantic_view(item) for item in value]
    return value


def _view(repo: Repository):
    with repo.transaction() as tx:
        ids = [r[0] for r in tx._db.execute("SELECT topic_id FROM topics ORDER BY topic_id")]
        cards = [tx.get_topic(topic_id, "replay").model_dump(mode="json") for topic_id in ids]
        changes = [e.model_dump(mode="json") for topic_id in ids for e in tx.topic_changes(topic_id)]
        reviews = [r.model_dump(mode="json") for r in tx.list_reviews(None)]
        overrides = [o.model_dump(mode="json") for source_id in FLOW_IDS for o in tx.get_overrides(source_id)]
        return semantic_view(dict(topics=cards, changes=changes, reviews=reviews, overrides=overrides,
                                  catalog_revision=tx.catalog_snapshot().catalog_revision))


def _import(repo, tickets, name):
    manifest = canonical(dict(dataset_id=name, synthetic=True,
                              tickets=[t.model_dump(mode="json") for t in tickets])).encode("utf-8")
    return import_tickets(manifest, dry_run=False, repository=repo)


def _run(service: RunService, repo: Repository, *, allow_errors=False):
    started = service.start(RunRequest())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = repo.get_run(started.run_id)
        if result.state not in {"queued", "running"}:
            if result.state != "completed" and not allow_errors:
                code = result.errors[0].code if result.errors else result.failure_code or "SCENARIO_RUN_FAILED"
                raise GatewayUnavailable(code, "Этап сценария не завершён. Проверьте код ошибки; новый набор не активирован.")
            return result
        time.sleep(.005)
    raise GatewayUnavailable("SCENARIO_RUN_TIMEOUT", "Сценарный worker не завершился вовремя; данные проверки оставлены отдельно.")


def run_flow(spec: ScenarioSpec, database: Path, gateway, *, unknown_check=False) -> list[dict]:
    require(not database.exists(), "SCENARIO_DATABASE_NOT_EMPTY")
    settings, profile = scenario_profile(spec, database)
    initialize(database)
    repo = Repository(database)
    require(repo.list_topic_ids(None) == [] and repo.list_imports() == [])
    adapters = build_text_pipeline(gateway, profile, settings)
    trace = []
    service = None
    with fixed_clock(spec.clock):
        try:
            service = RunService(repo, "replay", adapters)
            def observe(step, run=None, **details):
                trace.append(dict(step=step, state=_view(repo), details=details,
                    run=None if run is None else semantic_view(run.model_dump(mode="json"))))

            require(_import(repo, spec.base, "synthetic-flow-base").counts.inserted == 3)
            base = _run(service, repo)
            require(base.state == "completed")
            first = repo.get_source(FLOW_IDS[0])
            ids = {}
            for item in spec.authored_needs[FLOW_IDS[0]]:
                need = next(n for n in first.needs if n.evidence_quote == item.quote)
                from .replay import stable_id
                ids[item.label] = stable_id("topic", need.need_id)
            export_id, filters_id = ids["export"], ids["filters"]
            export = repo.get_topic(export_id, "replay")
            require(export.stats.ticket_count == 2 and repo.get_topic(filters_id, "replay").stats.ticket_count == 1)
            require(len(repo.list_reviews()) == 1 and len(repo.list_topic_ids(None)) == 2)
            observe("base", base)

            decision = DecisionUpdate(version=export.topic.version, status="needs_research",
                owner="Синтетический владелец", reason="Проверены источники сценария")
            repo.record_decision(export_id, decision)
            history = repo.get_topic(export_id, "replay").history
            try:
                repo.record_decision(export_id, decision)
                require(False)
            except RepositoryError as exc:
                require(exc.code == "VERSION_CONFLICT")
            observe("decision", stale_decision=409)

            export = repo.get_topic(export_id, "replay")
            passport = export.topic.passport.model_copy(update={"version": export.topic.passport.version + 1,
                "scope_confirmed": True, "scope": "Подтверждённые границы синтетической выгрузки"})
            repo.update_passport(export_id, passport, expected_version=export.topic.version,
                author="Синтетический аналитик", reason="Уточнить границы до delta", title="Подтверждённая синтетическая выгрузка")
            observe("passport")

            require(_import(repo, spec.base, "synthetic-flow-base").counts.unchanged == 3)
            repeated = _run(service, repo)
            require(repeated.state == "completed" and repeated.completed_steps == 0 and repeated.reused_steps == 10)
            observe("repeat", repeated)

            require(_import(repo, [spec.delta], "synthetic-flow-delta").counts.inserted == 1)
            delta = _run(service, repo)
            export = repo.get_topic(export_id, "replay")
            require(delta.state == "completed" and export.stats.ticket_count == 3 and export.history == history)
            require(export.topic.passport == passport)
            observe("delta", delta)

            workflow = HumanWorkflow(repo)
            review = repo.list_reviews()[0]
            command = ResolveReviewRequest(version=review.version, action="attach", topic_id=export_id,
                topic_versions={export_id: export.topic.version}, author="Синтетический аналитик", reason="Ручное назначение по источнику")
            resolved = workflow.resolve_review(review.review_id, command)
            require(workflow.resolve_review(review.review_id, command) == resolved)
            require(repo.get_topic(export_id, "replay").stats.ticket_count == 4)
            observe("review")

            export = repo.get_topic(export_id, "replay")
            need = repo.get_source(FLOW_IDS[1]).needs[0]
            command = ExcludeNeedsRequest(version=export.topic.version, need_ids=[need.need_id],
                author="Синтетический аналитик", reason="Исключить неверную связь сценария")
            excluded = workflow.exclude_needs(export_id, command)
            require(workflow.exclude_needs(export_id, command) == excluded)
            require(repo.get_topic(export_id, "replay").stats.ticket_count == 3)
            after_exclude = _run(service, repo)
            require(after_exclude.state == "completed" and after_exclude.completed_steps == 0)
            require(repo.get_topic(export_id, "replay").stats.ticket_count == 3)
            observe("exclude", after_exclude)

            export = repo.get_topic(export_id, "replay")
            filters = repo.get_topic(filters_id, "replay")
            preview = workflow.merge_preview(filters_id, export_id, "replay")
            require(preview.target.history == history)
            command = MergeTopicsRequest(version=filters.topic.version, target_topic_id=export_id,
                target_version=export.topic.version, confirm_decision_conflict=True,
                author="Синтетический аналитик", reason="Синтетический merge с общим source_id")
            merged = workflow.merge_topics(filters_id, command, "replay")
            require(workflow.merge_topics(filters_id, command, "replay") == merged)
            require(repo.get_topic(export_id, "replay").stats.ticket_count == 3)
            require(repo.get_topic(export_id, "replay").history == history)
            observe("merge", shared_source_deduplicated=True)

            saved = repo.get_topic(export_id, "replay")
            service.close()
            service = None
            repo = Repository(database)
            require(repo.get_topic(export_id, "replay") == saved)
            service = RunService(repo, "replay", build_text_pipeline(gateway, profile, settings))
            restarted = _run(service, repo)
            require(restarted.state == "completed" and restarted.completed_steps == 0 and restarted.reused_steps == 13)
            require(repo.get_topic(export_id, "replay") == saved)
            require(repo.get_topic(filters_id, "replay").topic.canonical_topic_id == export_id)
            observe("restart", restarted)

            if unknown_check:
                changed = spec.delta.model_copy(update={"text": spec.delta.text + " Synthetic unknown replay input."})
                require(_import(repo, [changed], "synthetic-flow-unknown").counts.updated == 1)
                failed = _run(service, repo, allow_errors=True)
                require(failed.state == "completed_with_errors" and any(e.code == "REPLAY_RECORD_MISSING" for e in failed.errors),
                        "SCENARIO_UNKNOWN_RECORD_NOT_REJECTED")
                after = repo.get_topic(export_id, "replay")
                require(after.history == history and after.topic.passport == passport and after.pending_source_ids == [FLOW_IDS[3]])
            with repo._session() as db:
                require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
                require(db.execute("PRAGMA foreign_key_check").fetchall() == [])
            return trace
        finally:
            if service is not None:
                service.close()
