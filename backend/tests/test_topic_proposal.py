"""Серверный стык new. Синтетические адаптеры не являются реализацией Analysis U2."""

import json
import sqlite3
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import REPO_ROOT
from app.gateways import GatewayUnavailable, TextRequest
from app.ingestion.integration import _CheckedGateway, build_text_profile
from app.main import create_app
from app.replay import ReplayGateway, make_record, publish_records, request_key, stable_id, verify_recordings
from app.repositories import Repository
from app.runs import RunService
from app.schemas import (
    AssignmentResult, DecisionUpdate, MaterialError, ProposedTopic, RunRequest,
)
from pipeline_adapters import TestComponents
from test_pipeline import setup, wait_run


def proposed_payload():
    return json.loads((REPO_ROOT / "data/contracts/topic-proposal.json").read_text(encoding="utf-8"))["value"]


class ProposalComponents(TestComponents):
    def __init__(self):
        super().__init__()
        self.profile = self.profile.model_copy(update={"prompt_hashes": {
            **self.profile.prompt_hashes, "propose_topic": "c" * 64}})
        self.mutate = lambda result: result

    def assign_topic(self, need, topic_passports):
        old = super().assign_topic(need, topic_passports)
        p = old.new_topic.passport
        proposal = ProposedTopic(title=old.new_topic.title, summary=old.new_topic.summary,
            scenario=p.scenario, need_statement=p.need_statement, scope=p.scope,
            inclusion_examples=p.inclusion_examples, exclusion_examples=p.exclusion_examples)
        meta = old.metadata[0].model_copy(update={"prompt_hash": "c" * 64})
        return self.mutate(AssignmentResult(assignment=old.assignment, proposed_topic=proposal,
            metadata=old.metadata + [meta]))


@pytest.mark.parametrize("field,value", [
    ("topic_id", "model-id"), ("status", "implemented"), ("owner", "model-owner"),
    ("version", 9), ("product_id", "other"), ("scope_confirmed", True), ("need_ids", []),
])
def test_proposal_cannot_set_server_or_human_fields(field, value):
    with pytest.raises(ValidationError):
        ProposedTopic.model_validate({**proposed_payload(), field: value})


def test_new_contract_has_one_payload_and_reads_legacy_checkpoint(fixture):
    adapter = TestComponents()
    document = adapter.normalize_ticket(fixture.sources[0], content_hash="a" * 64, source_version=1)
    need = adapter.extract_needs(document).needs[0]
    from app.schemas import CatalogSnapshot
    result = adapter.assign_topic(need, CatalogSnapshot(product_id="webneft", catalog_revision=0, topics=[]))
    old = result.model_dump(mode="json")
    old.pop("proposed_topic")
    assert AssignmentResult.model_validate(old).new_topic is not None
    with pytest.raises(ValidationError):
        AssignmentResult.model_validate({**old, "proposed_topic": proposed_payload()})
    with pytest.raises(ValidationError):
        AssignmentResult.model_validate({**old, "new_topic": None})
    with pytest.raises(ValidationError):
        AssignmentResult.model_validate({**old, "assignment": {**old["assignment"], "decision": "review"}})


def test_pipeline_builds_initial_topic_and_preserves_decision_on_restart(settings, fixture):
    repo = setup(settings, fixture, 1)
    adapter = ProposalComponents()
    with TestClient(create_app(settings, pipeline_adapters=adapter.adapters()), base_url="http://127.0.0.1") as client:
        run = wait_run(repo, client.post("/api/v1/runs", json={}).json()["run_id"])
        assert run.state == "completed" and run.origins == ["authored"]
        source = repo.get_source(fixture.sources[0].source_id)
        topic_id = stable_id("topic", source.needs[0].need_id)
        card = client.get("/api/v1/topics/" + topic_id).json()
        topic = card["topic"]
        assert topic["status"] == "new" and topic["owner"] is None and topic["version"] == 1
        assert topic["passport"]["version"] == 1 and not topic["passport"]["scope_confirmed"]
        assert card["stats"]["ticket_count"] == 1 and card["synthetic"] and card["data_origin"] == "authored"
        update = DecisionUpdate(version=1, status="needs_research", owner="Тестовый владелец", reason="Сохранить решение")
        saved = client.patch(f"/api/v1/topics/{topic_id}/decision", json=update.model_dump(mode="json"))
        assert saved.status_code == 200
        assert client.patch(f"/api/v1/topics/{topic_id}/decision", json=update.model_dump(mode="json")).status_code == 409
    before = Repository(repo.path).get_topic(topic_id, "replay")
    with TestClient(create_app(settings, pipeline_adapters=ProposalComponents().adapters()), base_url="http://127.0.0.1") as client:
        repeated = wait_run(repo, client.post("/api/v1/runs", json={}).json()["run_id"])
        assert repeated.reused_steps == 3 and repeated.completed_steps == 0
        assert Repository(repo.path).get_topic(topic_id, "replay") == before
    assert len(repo.list_topic_ids(None)) == 1 and len(before.history) == 1


@pytest.mark.parametrize("kind,code", [
    ("quote", "NEW_TOPIC_INVALID"), ("metadata", "ANALYSIS_PROVENANCE_MISSING"),
    ("profile", "PROPOSAL_PROFILE_MISSING"),
])
def test_invalid_proposal_does_not_publish_topic_or_assignment_checkpoint(settings, fixture, kind, code):
    repo = setup(settings, fixture, 1)
    adapter = ProposalComponents()
    def mutate(result):
        if kind == "quote":
            values = result.model_dump(mode="json")
            values["proposed_topic"]["inclusion_examples"][0]["evidence_refs"][0]["quote"] = "Выдуманная цитата"
            return AssignmentResult.model_validate(values)
        return result.model_copy(update={"metadata": result.metadata[:1]}) if kind == "metadata" else result
    adapter.mutate = mutate
    if kind == "profile":
        adapter.profile.prompt_hashes.pop("propose_topic")
    service = RunService(repo, "replay", adapter.adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.state == "completed_with_errors" and run.errors[0].code == code
        assert repo.list_topic_ids(None) == []
        with repo.transaction() as tx:
            assert tx._db.execute("SELECT COUNT(*) FROM stage_checkpoints WHERE stage='assignment'").fetchone()[0] == 0
    finally:
        service.close()


def test_proposal_checkpoint_failure_rolls_back_topic_and_retry_succeeds(settings, fixture, monkeypatch):
    repo = setup(settings, fixture, 1)
    original = Repository.save_checkpoint
    def fail(self, key, stage, run_id, payload):
        if stage == "assignment":
            raise sqlite3.OperationalError("injected")
        return original(self, key, stage, run_id, payload)
    monkeypatch.setattr(Repository, "save_checkpoint", fail)
    service = RunService(repo, "replay", ProposalComponents().adapters())
    try:
        first = wait_run(repo, service.start(RunRequest()).run_id)
        assert first.state == "completed_with_errors" and repo.list_topic_ids(None) == []
        monkeypatch.setattr(Repository, "save_checkpoint", original)
        repeated = wait_run(repo, service.start(RunRequest()).run_id)
        assert repeated.state == "completed" and repeated.reused_steps == 2
        assert len(repo.list_topic_ids(None)) == 1
    finally:
        service.close()


def test_failed_normalization_never_reaches_unchecked_extractor(settings, fixture):
    repo = setup(settings, fixture, 1)
    class BrokenNormalizer(TestComponents):
        def normalize_ticket(self, ticket, **context):
            doc = super().normalize_ticket(ticket, **context)
            return doc.model_copy(update={"errors": [MaterialError(source_id=ticket.source_id,
                stage="normalization", code="SYNTHETIC_FAILURE", message="Синтетическая ошибка")]})
    adapter = BrokenNormalizer()
    service = RunService(repo, "replay", adapter.adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.state == "completed_with_errors" and run.failed_materials == 1
        assert adapter.events == [("normalize", fixture.sources[0].source_id)]
        assert repo.get_source(fixture.sources[0].source_id).requires_analysis
    finally:
        service.close()


def test_proposal_replay_is_exact_and_preserves_old_recording(tmp_path):
    bundle = json.loads((REPO_ROOT / "data/recordings/examples/topic-proposal-authored.json").read_text(encoding="utf-8"))
    entry = bundle["entries"][0]
    req = TextRequest.model_validate(entry["request"])
    record = make_record(req, ProposedTopic, entry["response"], model_id=entry["model_id"],
        normalization_hash=entry["normalization_hash"], set_version=bundle["version"], origin="authored")
    publish_records(tmp_path, [record])
    gateway = ReplayGateway(tmp_path, entry["model_id"], entry["normalization_hash"])
    result = gateway.generate(req, ProposedTopic)
    assert result.metadata.origin == "authored" and result.value == ProposedTopic.model_validate(proposed_payload())
    assert verify_recordings(tmp_path)["record_count"] == 1
    with pytest.raises(GatewayUnavailable) as exc:
        gateway.generate(req.model_copy(update={"data": {**req.data, "catalog_revision": 1}}), ProposedTopic)
    assert exc.value.code == "REPLAY_RECORD_MISSING"
    with pytest.raises(GatewayUnavailable) as exc:
        make_record(req.model_copy(update={"operation": "assign_topic"}), ProposedTopic, entry["response"],
            model_id=entry["model_id"], normalization_hash=entry["normalization_hash"], set_version=bundle["version"], origin="authored")
    assert exc.value.code == "REPLAY_SCHEMA_MISMATCH"
    old = json.loads((REPO_ROOT / "data/recordings/examples/text-authored.json").read_text(encoding="utf-8"))["entries"][0]
    old_request = TextRequest.model_validate(old["request"])
    from app.schemas import ExtractedNeeds
    # Независимый расчёт прежнего envelope format=1: новая операция его не меняет.
    encoded = json.dumps({"format": 1, "request": old_request.model_dump(mode="json"),
        "model_id": old["model_id"], "normalization_hash": old["normalization_hash"],
        "response_schema": ExtractedNeeds.model_json_schema()}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert request_key(old_request, ExtractedNeeds, old["model_id"], old["normalization_hash"]) == sha256(encoded).hexdigest()


def test_missing_proposal_prompt_fails_before_any_gateway_call(settings):
    profile = build_text_profile(settings, model_id="synthetic", recording_set_version="test")
    assert "propose_topic" not in profile.prompt_hashes
    from app.ingestion.integration import _prompts
    checked = _CheckedGateway(None, profile, settings, _prompts())
    request = TextRequest(operation="propose_topic", prompt="Синтетический контракт", prompt_version="propose-topic-v1",
        data={}, parameters=profile.parameters)
    with pytest.raises(GatewayUnavailable) as exc:
        checked.generate(request, ProposedTopic)
    assert exc.value.code == "ANALYSIS_REQUEST_CONFLICT"


def test_optional_proposal_prompt_gets_own_profile_hash_and_checked_replay(settings, tmp_path, monkeypatch):
    import app.ingestion.integration as integration
    assign = tmp_path / "assign.md"
    assign.write_text(integration.DEFAULT_ASSIGN_PROMPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    prompt = "Синтетический тест интерфейса proposal, не промпт для живой модели."
    (tmp_path / "propose.md").write_text(prompt, encoding="utf-8")
    monkeypatch.setattr(integration, "DEFAULT_ASSIGN_PROMPT_PATH", assign)
    profile = integration.build_text_profile(settings, model_id="synthetic", recording_set_version="proposal-check-v1")
    assert profile.prompt_hashes["propose_topic"] == sha256(prompt.encode("utf-8")).hexdigest()
    req = TextRequest(operation="propose_topic", prompt=prompt, prompt_version="propose-topic-v1",
        data={"synthetic": True, "need": "contract-only"}, parameters=profile.parameters)
    record = make_record(req, ProposedTopic, proposed_payload(), model_id=profile.model_id,
        normalization_hash=profile.parameters["normalization_hash"], set_version=profile.recording_set_version, origin="authored")
    publish_records(tmp_path / "recordings", [record])
    gateway = ReplayGateway(tmp_path / "recordings", profile.model_id, profile.parameters["normalization_hash"])
    checked = _CheckedGateway(gateway, profile, settings, integration._prompts())
    result = checked.generate(req, ProposedTopic)
    assert result.metadata == record.metadata and result.value == ProposedTopic.model_validate(proposed_payload())


def test_claimed_complete_search_must_cover_actual_passports(settings, fixture):
    repo = setup(settings, fixture, 1)
    first = RunService(repo, "replay", TestComponents().adapters())
    try:
        assert wait_run(repo, first.start(RunRequest()).run_id).state == "completed"
    finally:
        first.close()
    from app.schemas import DatasetRegistration
    repo.register_dataset(DatasetRegistration(dataset_id="proposal-second", synthetic=True, tickets=fixture.sources[1:2]))
    adapter = ProposalComponents()
    # Для настоящего полного итога не разрешаем приписать поиску чужие счётчики.
    def claimed(result):
        return result.model_copy(update={"assignment": result.assignment.model_copy(update={"passport_versions": {}})})
    adapter.mutate = claimed
    service = RunService(repo, "replay", adapter.adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.state == "completed_with_errors" and any(e.code == "ASSIGNMENT_INVALID" for e in run.errors)
        assert len(repo.list_topic_ids(None)) == 1
    finally:
        service.close()
