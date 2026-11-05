import sqlite3
import time
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.db import connection, initialize
from app.config import REPO_ROOT
from app.errors import RepositoryError
from app.main import create_app
from app.replay import stable_id
from app.repositories import Repository
from app.runs import RunService
from app.schemas import DatasetRegistration, DecisionUpdate, RunRequest
from pipeline_adapters import TestComponents


def setup(settings, fixture, count=2):
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repo = Repository(path)
    repo.register_dataset(DatasetRegistration(dataset_id="pipeline-test", synthetic=True, tickets=list(reversed(fixture.sources[:count]))))
    return repo


def wait_run(repo, run_id):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        run = repo.get_run(run_id)
        if run.state not in {"queued", "running"}:
            return run
        time.sleep(.01)
    raise AssertionError("Worker did not finish")


def test_api_without_integrations_is_honest_and_does_not_create_run(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        body = json.loads((REPO_ROOT / "data/contracts/run.json").read_text(encoding="utf-8"))
        result = client.post("/api/v1/runs", json=body)
        assert result.status_code == 503 and result.json()["code"] == "INTEGRATION_NOT_READY"
        assert client.app.state.repository.active_run() is None
        health = client.get("/api/v1/health").json()
        assert health["pipeline_ready"] is False and health["processing_state"] == "idle"
        assert health["integration_errors"] == ["NORMALIZER_NOT_CONNECTED", "ANALYSIS_NOT_CONNECTED"]
        assert client.get("/api/v1/runs/missing").status_code == 404
        for body in ({"mode": "live"}, {"product_id": "other"}, {"date_from": "2026-11-05", "date_to": "2026-11-04"}):
            assert client.post("/api/v1/runs", json=body).status_code == 422


def test_two_pass_order_reuse_and_decisions_after_reopen(settings, fixture):
    repo = setup(settings, fixture)
    adapter = TestComponents()
    with TestClient(create_app(settings, pipeline_adapters=adapter.adapters()), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/runs", json={})
        assert response.status_code == 202 and response.json()["mode"] == "replay"
        run = wait_run(repo, response.json()["run_id"])
        assert run.state == "completed" and run.successful_materials == 2 and run.coverage == 1
        assert run.origins == ["authored"] and run.completed_steps == 6
        assert adapter.events == [(s, i) for i in [fixture.sources[0].source_id, fixture.sources[1].source_id] for s in ("normalize", "extract")] + [
            ("assign", fixture.sources[0].source_id), ("assign", fixture.sources[1].source_id)]
        topic_id = repo.list_topic_ids(None)[0]
        card = repo.get_topic(topic_id, "replay")
        repo.record_decision(topic_id, DecisionUpdate(status="needs_research", owner="Владелец", reason="Сохранить решение", version=card.topic.version))
        before = repo.get_topic(topic_id, "replay")
        second = client.post("/api/v1/runs", json={}).json()
        repeat = wait_run(repo, second["run_id"])
        assert repeat.state == "completed" and repeat.reused_steps == 6 and repeat.completed_steps == 0
        assert len(adapter.events) == 6
        assert client.get("/api/v1/runs/" + run.run_id).json()["state"] == "completed"
    assert Repository(repo.path).get_topic(topic_id, "replay") == before


def test_active_click_and_import_race_are_serialized(settings, fixture):
    repo = setup(settings, fixture, 1)
    adapter = TestComponents()
    adapter.block = "extract"
    with TestClient(create_app(settings, pipeline_adapters=adapter.adapters()), base_url="http://127.0.0.1") as client:
        first = client.post("/api/v1/runs", json={}).json()
        try:
            assert adapter.entered.wait(2)
            with ThreadPoolExecutor(max_workers=4) as pool:
                clicks = list(pool.map(lambda _: client.post("/api/v1/runs", json={}), range(4)))
            assert all(r.status_code == 202 and r.json()["run_id"] == first["run_id"] for r in clicks)
            assert client.get("/api/v1/health").json()["processing_state"] == "busy"
            with pytest.raises(RepositoryError) as exc:
                repo.register_dataset(DatasetRegistration(dataset_id="new", synthetic=True, tickets=[fixture.sources[1]]))
            assert exc.value.code == "RUN_ACTIVE"
        finally:
            adapter.release.set()
        assert wait_run(repo, first["run_id"]).state == "completed"
        # Та же блокировка доступна будущему импорту вокруг файловой подготовки/commit.
        with repo.import_commit():
            blocked = client.post("/api/v1/runs", json={})
            assert blocked.status_code == 409 and blocked.json()["code"] == "IMPORT_ACTIVE"


def test_interrupt_between_passes_and_resume_exact_successes(settings, fixture):
    repo = setup(settings, fixture, 1)
    adapter = TestComponents()
    adapter.block = "assign"
    service = RunService(repo, "replay", adapter.adapters())
    run = service.start(RunRequest())
    try:
        assert adapter.entered.wait(2)
        service.close()
        assert repo.get_run(run.run_id).state == "interrupted"
    finally:
        adapter.release.set()
        service.futures[run.run_id].result(timeout=2)
    assert repo.list_topic_ids(None) == []
    resumed_adapter = TestComponents()
    with TestClient(create_app(settings, pipeline_adapters=resumed_adapter.adapters()), base_url="http://127.0.0.1") as client:
        fresh = client.post("/api/v1/runs", json={}).json()
        restored = wait_run(repo, fresh["run_id"])
        assert restored.state == "completed" and restored.reused_steps == 2 and restored.completed_steps == 1
        assert resumed_adapter.events == [("assign", fixture.sources[0].source_id)]
        assert len(repo.list_topic_ids(None)) == 1
    assert repo.get_run(run.run_id).state == "interrupted"


@pytest.mark.parametrize("stage", ["normalize", "extract", "assign"])
def test_stage_failures_are_visible_and_successes_not_duplicated(settings, fixture, stage):
    repo = setup(settings, fixture, 1)
    adapter = TestComponents()
    adapter.fail = stage
    service = RunService(repo, "replay", adapter.adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.state == "completed_with_errors" and run.errors
        assert "SENSITIVE_TEST_SENTINEL" not in run.model_dump_json()
        assert not repo.list_topic_ids(None)
        adapter.fail = None
        retry = wait_run(repo, service.start(RunRequest()).run_id)
        assert retry.state == "completed" and len(repo.list_topic_ids(None)) == 1
        if stage != "normalize":
            assert adapter.events.count(("normalize", fixture.sources[0].source_id)) == 1
    finally:
        service.close()


def test_assignment_checkpoint_and_link_rollback_together(settings, fixture, monkeypatch):
    repo = setup(settings, fixture, 1)
    original = Repository.save_checkpoint
    def fail(self, key, stage, run_id, payload):
        if stage == "assignment":
            raise sqlite3.OperationalError("injected checkpoint failure")
        return original(self, key, stage, run_id, payload)
    monkeypatch.setattr(Repository, "save_checkpoint", fail)
    service = RunService(repo, "replay", TestComponents().adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.errors and repo.list_topic_ids(None) == []
        with connection(repo.path) as db:
            assert db.execute("SELECT count(*) FROM topic_need_links").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM stage_checkpoints WHERE stage='assignment'").fetchone()[0] == 0
    finally:
        service.close()


def test_period_filter_and_deterministic_ids(settings, fixture):
    repo = setup(settings, fixture)
    service = RunService(repo, "replay", TestComponents().adapters())
    try:
        day = fixture.sources[1].created_at.date()
        run = wait_run(repo, service.start(RunRequest(date_from=day, date_to=day)).run_id)
        assert run.total_materials == 1 and run.state == "completed"
        source = repo.get_source(fixture.sources[1].source_id)
        expected = stable_id("need", "webneft", source.ticket.source_id, source.source_version, source.content_hash, 0)
        assert source.needs[0].need_id == expected
        assert repo.list_topic_ids(None) == [stable_id("topic", expected)]
    finally:
        service.close()


def test_matching_new_source_preserves_existing_decision_and_manual_exclusion(settings, fixture):
    from datetime import datetime, timezone
    from app.schemas import ManualOverride
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    topic_id = fixture.topic.topic_id
    repo.record_decision(topic_id, DecisionUpdate(status="needs_research", owner="Владелец", reason="Не терять", version=1))
    before = repo.get_topic(topic_id, "replay")
    ticket = fixture.sources[0].model_copy(update={"source_id": "DELTA-PIPELINE", "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc)})
    repo.register_dataset(DatasetRegistration(dataset_id="delta", synthetic=True, tickets=[ticket]))
    adapter = TestComponents(target=topic_id)
    service = RunService(repo, "replay", adapter.adapters())
    request = RunRequest(date_from=date(2030, 1, 1))
    try:
        assert wait_run(repo, service.start(request).run_id).state == "completed"
        saved = repo.get_topic(topic_id, "replay")
        assert saved.stats.ticket_count == 5 and saved.history == before.history
        assert saved.topic.owner == before.topic.owner and saved.topic.status == before.topic.status
        need_id = repo.get_source(ticket.source_id).needs[0].need_id
        repo.record_override(ManualOverride(override_id="manual-test", source_id=ticket.source_id, source_version=1,
            need_id=need_id, topic_id=topic_id, action="exclude", author="Человек", reason="Ручное решение", created_at=datetime.now(timezone.utc)),
            expected_versions={topic_id: saved.topic.version})
        assert wait_run(repo, service.start(request).run_id).state == "completed"
        restored = repo.get_topic(topic_id, "replay")
        assert restored.stats.ticket_count == 4 and restored.history == before.history
        assert repo.get_overrides(ticket.source_id)[0].action == "exclude"
    finally:
        service.close()


def test_review_is_not_duplicated_on_repeat(settings, fixture):
    repo = setup(settings, fixture, 1)
    service = RunService(repo, "replay", TestComponents(decision="review").adapters())
    try:
        assert wait_run(repo, service.start(RunRequest()).run_id).state == "completed"
        reviews = repo.list_reviews()
        assert len(reviews) == 1 and repo.list_topic_ids(None) == []
        assert wait_run(repo, service.start(RunRequest()).run_id).reused_steps == 3
        assert repo.list_reviews() == reviews
    finally:
        service.close()


def test_invalid_quote_does_not_create_needs_or_checkpoint(settings, fixture):
    repo = setup(settings, fixture, 1)
    class InvalidQuote(TestComponents):
        def extract_needs(self, document):
            result = super().extract_needs(document)
            return result.model_copy(update={"needs": [result.needs[0].model_copy(update={"evidence_quote": "invented quote"})]})
    service = RunService(repo, "replay", InvalidQuote().adapters())
    try:
        result = wait_run(repo, service.start(RunRequest()).run_id)
        assert result.state == "completed_with_errors" and result.errors[0].code == "INVALID_EVIDENCE"
        assert repo.get_source(fixture.sources[0].source_id).needs == []
        with connection(repo.path) as db:
            assert db.execute("SELECT count(*) FROM stage_checkpoints WHERE stage='extraction'").fetchone()[0] == 0
    finally: service.close()


def test_live_cannot_use_authored_test_result(settings, fixture):
    repo = setup(settings, fixture, 1)
    service = RunService(repo, "live", TestComponents().adapters())
    try:
        result = wait_run(repo, service.start(RunRequest()).run_id)
        assert result.mode == "live" and result.state == "completed_with_errors"
        assert result.errors[0].code == "ANALYSIS_PROVENANCE_CONFLICT"
        assert not repo.list_topic_ids(None)
    finally: service.close()


def test_empty_success_and_extraction_error_are_distinct(settings, fixture):
    from app.schemas import ExtractionResult
    repo = setup(settings, fixture, 1)
    class Empty(TestComponents):
        def extract_needs(self, document):
            self.event("extract", document.source_id)
            return ExtractionResult(needs=[], metadata=[self.meta(document.model_dump(mode="json"))])
    service = RunService(repo, "replay", Empty().adapters())
    try:
        result = wait_run(repo, service.start(RunRequest()).run_id)
        assert result.state == "completed" and not result.errors
        assert not repo.get_source(fixture.sources[0].source_id).requires_analysis
        assert repo.list_topic_ids(None) == []
    finally: service.close()


def test_prompt_fingerprint_cannot_claim_different_actual_prompt(settings, fixture):
    repo = setup(settings, fixture, 1)
    adapter = TestComponents()
    adapter.profile = adapter.profile.model_copy(update={"prompt_hashes": {"extract_needs": "c" * 64, "assign_topic": "a" * 64}})
    service = RunService(repo, "replay", adapter.adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.state == "completed_with_errors"
        assert run.errors[0].code == "ANALYSIS_PROVENANCE_CONFLICT"
        assert repo.get_source(fixture.sources[0].source_id).needs == []
    finally: service.close()


def test_catalog_change_before_commit_is_visible_and_retryable(settings, fixture):
    from datetime import datetime, timezone
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    topic_id = fixture.topic.topic_id
    repo.record_decision(topic_id, DecisionUpdate(status="needs_research", owner="Владелец", reason="Сохранить", version=1))
    before = repo.get_topic(topic_id, "replay")
    ticket = fixture.sources[0].model_copy(update={"source_id": "CATALOG-DELTA", "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc)})
    repo.register_dataset(DatasetRegistration(dataset_id="catalog-delta", synthetic=True, tickets=[ticket]))
    class CatalogMutation(TestComponents):
        mutate = True
        def assign_topic(self, need, topic_passports):
            result = super().assign_topic(need, topic_passports)
            if self.mutate:
                self.mutate = False
                current = repo.get_topic(topic_id, "replay").topic
                repo.update_passport(topic_id, current.passport.model_copy(update={"version": current.passport.version + 1}),
                    expected_version=current.version, title="Ручная правка паспорта", author="Человек", reason="Уточнение")
            return result
    adapter = CatalogMutation(target=topic_id)
    service = RunService(repo, "replay", adapter.adapters())
    try:
        request = RunRequest(date_from=date(2030, 1, 1))
        run = wait_run(repo, service.start(request).run_id)
        assert run.state == "completed_with_errors" and run.errors[0].code == "CATALOG_CHANGED"
        assert repo.get_topic(topic_id, "replay").stats.ticket_count == 4
        retry = wait_run(repo, service.start(request).run_id)
        assert retry.state == "completed" and retry.reused_steps == 2
        saved = repo.get_topic(topic_id, "replay")
        assert saved.topic.title == "Ручная правка паспорта" and saved.history == before.history
        assert saved.stats.ticket_count == 5 and saved.topic.owner == "Владелец"
    finally: service.close()


def test_updated_source_does_not_silently_cancel_manual_exclusion(settings, fixture):
    from datetime import datetime, timezone
    from app.schemas import ManualOverride
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    target = fixture.topic.topic_id
    ticket = fixture.sources[0].model_copy(update={"source_id": "MANUAL-DELTA", "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc)})
    repo.register_dataset(DatasetRegistration(dataset_id="manual-delta", synthetic=True, tickets=[ticket]))
    adapter = TestComponents(target=target)
    service = RunService(repo, "replay", adapter.adapters())
    request = RunRequest(date_from=date(2030, 1, 1))
    try:
        assert wait_run(repo, service.start(request).run_id).state == "completed"
        need_id = repo.get_source(ticket.source_id).needs[0].need_id
        card = repo.get_topic(target, "replay")
        repo.record_override(ManualOverride(override_id="exclude-old-version", source_id=ticket.source_id, source_version=1,
            need_id=need_id, topic_id=target, action="exclude", author="Человек", reason="Не присоединять", created_at=datetime.now(timezone.utc)),
            expected_versions={target: card.topic.version})
        ticket = ticket.model_copy(update={"text": ticket.text + " Новая версия текста."})
        repo.register_dataset(DatasetRegistration(dataset_id="manual-delta", synthetic=True, tickets=[ticket]))
        assert wait_run(repo, service.start(request).run_id).state == "completed"
        assert repo.get_topic(target, "replay").stats.ticket_count == 4
        assert adapter.events.count(("assign", ticket.source_id)) == 1
        reviews = repo.list_reviews()
        assert len(reviews) == 1 and reviews[0].candidate_topic_ids == [target]
        assert reviews[0].need_ids == [repo.get_source(ticket.source_id).needs[0].need_id]
        assert repo.get_overrides(ticket.source_id)[0].requires_recheck
        assert wait_run(repo, service.start(request).run_id).reused_steps == 3
        assert repo.list_reviews() == reviews
    finally: service.close()
