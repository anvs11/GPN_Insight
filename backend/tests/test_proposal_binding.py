"""Настоящая сборка U2 на synthetic/authored replay, без live-модели."""

import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.db import initialize
from app.gateways import GatewayResult, GatewayUnavailable
import app.ingestion.integration as integration
from app.ingestion.normalizer import TextNormalizer
from app.main import create_app
from app.replay import ReplayGateway, make_record, publish_records, stable_id
from app.repositories import Repository
from app.runs import RunService
from app.schemas import (
    BatchAssignment, CatalogSnapshot, DatasetRegistration, DecisionUpdate,
    EvidenceRef, ExtractedNeed, ExtractedNeeds, PassportExample, ProposedTopic,
    RunRequest, TicketInput,
)
from pipeline_adapters import TestComponents
from scripts.check_text_pipeline import PERIOD, wait_run
from scripts.text_pipeline_fixture import seed_existing_topic
from test_pipeline import setup


DECISION = dict(status="needs_research", owner="Synthetic reviewer",
                reason="Synthetic persistence check")


class ProposalFixtureGateway:
    """Создаёт только authored-записи для явно известных тестовых источников."""

    def __init__(self, profile):
        self.profile = profile
        self.records = []

    def generate(self, request, response_schema):
        if request.operation == "extract_needs":
            doc = request.data["document"]
            assert doc["source_id"] in {"SYNTHETIC-PROPOSAL-1", "SYNTHETIC-PROPOSAL-2"}
            fragment = doc["fragments"][0]
            value = ExtractedNeeds(needs=[ExtractedNeed(
                need_id="pending", source_id=doc["source_id"],
                fragment_id=fragment["fragment_id"], evidence_quote=fragment["text"],
                scenario="Synthetic export", statement="Synthetic export request",
                need_type="feature_request", explicit_request=True)])
        else:
            need = request.data["need"]
            ref = EvidenceRef(source_id=need["source_id"], fragment_id=need["fragment_id"],
                              quote=need["evidence_quote"])
            if request.operation == "assign_topic":
                candidates = [t["topic_id"] for t in request.data["topics"]
                              if t["title"] == "Synthetic proposed export"]
                value = BatchAssignment(
                    decision="match" if candidates else "provisional_no_match",
                    matched_topic_id=candidates[0] if candidates else None,
                    candidate_topic_ids=candidates, reason="Synthetic authored decision",
                    evidence_refs=[ref])
            else:
                assert request.operation == "propose_topic"
                value = ProposedTopic(title="Synthetic proposed export", summary=need["statement"],
                    scenario=need["scenario"], need_statement=need["statement"], scope="Synthetic export only",
                    inclusion_examples=[PassportExample(text=need["evidence_quote"], evidence_refs=[ref])])
        record = make_record(request, response_schema, value.model_dump(mode="json"),
            model_id=self.profile.model_id, normalization_hash=self.profile.parameters["normalization_hash"],
            set_version=self.profile.recording_set_version, origin="authored")
        self.records.append(record)
        return GatewayResult(value=value, metadata=record.metadata)


def prepare_proposal_replay(folder, *, warm=True):
    """Отдельная capture-БД; рабочий сервер получает только строгий ReplayGateway."""
    settings = Settings(_env_file=None, app_db_path=folder / "capture.db", app_model_mode="replay")
    profile = integration.build_text_profile(settings, model_id="synthetic-proposal-no-live-model",
        recording_set_version="proposal-binding-authored-v2")
    tickets = [TicketInput(source_id=f"SYNTHETIC-PROPOSAL-{i}",
        created_at=f"2026-10-0{i}T12:00:00+03:00", text=f"Synthetic export request {i}.")
        for i in (1, 2)]
    initialize(settings.app_db_path)
    repo = Repository(settings.app_db_path)
    if warm:
        seed_existing_topic(repo, TextNormalizer(profile))
    gateway = ProposalFixtureGateway(profile)
    service = RunService(repo, "replay", integration.build_text_pipeline(gateway, profile, settings))
    try:
        topic_id = None
        for i, ticket in enumerate(tickets, 1):
            repo.register_dataset(DatasetRegistration(dataset_id=f"synthetic-proposal-{i}",
                synthetic=True, tickets=[ticket]))
            run = wait_run(repo, service.start(RunRequest.model_validate(PERIOD)).run_id)
            assert run.state == "completed", run.errors
            if i == 1:
                topic_id = stable_id("topic", repo.get_source(ticket.source_id).needs[0].need_id)
                card = repo.get_topic(topic_id, "replay")
                repo.record_decision(topic_id, DecisionUpdate(version=card.topic.version, **DECISION))
        assert repo.get_topic(topic_id, "replay").stats.ticket_count == 2
    finally:
        service.close()
    recordings = folder / "recordings"
    publish_records(recordings, gateway.records)
    profile_path = folder / "profile.json"
    profile_path.write_text(profile.model_dump_json(), encoding="utf-8")
    return profile, profile_path, recordings, tickets, topic_id


@pytest.mark.parametrize("custom_prompt", [False, True])
def test_proposal_uses_same_real_prompt_in_profile_and_service(settings, tmp_path, monkeypatch, custom_prompt):
    path = REPO_ROOT / "backend/prompts/propose.md"
    if custom_prompt:
        path = tmp_path / "custom-propose.md"
        path.write_text("Synthetic custom proposal prompt", encoding="utf-8")
    monkeypatch.setattr(integration, "DEFAULT_PROPOSE_PROMPT_PATH", path if custom_prompt else None)
    profile = integration.build_text_profile(settings, model_id="synthetic", recording_set_version="binding-v1")
    assert profile.prompt_hashes["propose_topic"] == sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
    gateway = ProposalFixtureGateway(profile)
    adapters = integration.build_text_pipeline(gateway, profile, settings)
    assert adapters.analysis.service.propose_prompt_path == path
    seed = seed_existing_topic(repository_for_seed(settings), adapters.normalizer)
    need = Repository(settings.app_db_path).get_source("SYNTHETIC-TEXT-INTEGRATION-SEED").needs[0]
    result = adapters.analysis.assign_topic(need, CatalogSnapshot(product_id="webneft",
        catalog_revision=1, topics=[seed.topic]))
    assert result.assignment.decision == "new" and result.proposed_topic is not None
    assert [r.request.operation for r in gateway.records] == ["assign_topic", "propose_topic"]
    assert gateway.records[-1].request.prompt == path.read_text(encoding="utf-8")


def repository_for_seed(settings):
    initialize(settings.app_db_path)
    return Repository(settings.app_db_path)


def test_old_two_prompt_runtime_profile_is_not_silently_upgraded(settings):
    profile = integration.build_text_profile(settings, model_id="synthetic", recording_set_version="old-v1")
    profile.prompt_hashes.pop("propose_topic")
    with pytest.raises(GatewayUnavailable) as exc:
        integration.build_text_pipeline(None, profile, settings)
    assert exc.value.code == "ANALYSIS_PROFILE_CONFLICT"
    assert "propose_topic" not in profile.prompt_hashes


@pytest.mark.parametrize("warm", [False, True])
def test_ordinary_app_proposal_replay_delta_decision_and_reopen(tmp_path, monkeypatch, warm):
    profile, profile_path, recordings, tickets, topic_id = prepare_proposal_replay(tmp_path, warm=warm)
    settings = Settings(_env_file=None, app_db_path=tmp_path / "verify.db", app_model_mode="replay",
        app_processing_profile_path=profile_path, app_recordings_root=recordings,
        app_replay_model_id=profile.model_id, app_replay_normalization_hash=profile.parameters["normalization_hash"])
    repo = repository_for_seed(settings)
    if warm:
        seed_existing_topic(repo, TextNormalizer(profile))
    else:
        assert repo.list_topic_ids(None) == []
    monkeypatch.setattr(ProposalFixtureGateway, "generate", lambda *a, **k: pytest.fail("Authored fixture reached server"))
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/health").json()["pipeline_ready"]
        for i, ticket in enumerate(tickets, 1):
            manifest = dict(dataset_id=f"synthetic-proposal-{i}", synthetic=True,
                            tickets=[ticket.model_dump(mode="json")])
            response = client.post("/api/v1/imports", files={"manifest": ("manifest.json",
                json.dumps(manifest).encode(), "application/json")})
            assert response.status_code == 200
            run = wait_run(repo, client.post("/api/v1/runs", json=PERIOD).json()["run_id"])
            assert run.state == "completed" and run.origins == ["authored"]
            card = client.get("/api/v1/topics/" + topic_id).json()
            assert card["stats"]["ticket_count"] == i
            if i == 1:
                assert card["topic"]["owner"] is None and card["topic"]["version"] == 1
                assert card["topic"]["status"] == "new" and not card["topic"]["passport"]["scope_confirmed"]
                saved = client.patch(f"/api/v1/topics/{topic_id}/decision",
                    json=dict(version=1, **DECISION))
                assert saved.status_code == 200
                history = saved.json()["history"]
            else:
                assert card["topic"]["owner"] == DECISION["owner"] and card["history"] == history
        saved_card = client.get("/api/v1/topics/" + topic_id).json()
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        run = wait_run(repo, client.post("/api/v1/runs", json=PERIOD).json()["run_id"])
        assert run.state == "completed" and run.reused_steps == 6 and run.completed_steps == 0
        assert client.get("/api/v1/topics/" + topic_id).json() == saved_card


@pytest.mark.parametrize("profile_has_propose", [False, True])
def test_legacy_new_topic_checkpoint_reuses_without_fabricated_proposal(settings, fixture, profile_has_propose):
    class Legacy(TestComponents):
        def assign_topic(self, need, snapshot):
            result = super().assign_topic(need, snapshot)
            return result.model_copy(update={"metadata": result.metadata[:1]})

    adapter = Legacy()
    if not profile_has_propose:
        adapter.profile.prompt_hashes.pop("propose_topic")
    repo = setup(settings, fixture, 1)
    service = RunService(repo, "replay", adapter.adapters())
    try:
        first = wait_run(repo, service.start(RunRequest()).run_id)
        assert first.state == "completed", first.errors
        topic_id = repo.list_topic_ids(None)[0]
        card = repo.get_topic(topic_id, "replay")
        repo.record_decision(topic_id, DecisionUpdate(version=card.topic.version, **DECISION))
        saved = repo.get_topic(topic_id, "replay")
        # Исторический JSON не содержал даже optional proposed_topic=null.
        with repo.transaction() as tx:
            row = tx._db.execute("SELECT checkpoint_key,body_json FROM stage_checkpoints WHERE stage='assignment'").fetchone()
            payload = json.loads(row[1])
            payload.pop("proposed_topic", None)
            tx._db.execute("UPDATE stage_checkpoints SET body_json=? WHERE checkpoint_key=?",
                           (json.dumps(payload), row[0]))
        adapter.events.clear()
    finally:
        service.close()
    service = RunService(Repository(repo.path), "replay", adapter.adapters())
    try:
        repeated = wait_run(repo, service.start(RunRequest()).run_id)
        assert repeated.state == "completed" and repeated.reused_steps == 3
        assert repeated.completed_steps == 0 and adapter.events == []
        assert Repository(repo.path).get_topic(topic_id, "replay") == saved
    finally:
        service.close()


@pytest.mark.parametrize("invalid", ["hash", "model", "live-authored"])
def test_legacy_compatibility_still_rejects_false_provenance(settings, fixture, invalid):
    class ForgedLegacy(TestComponents):
        def assign_topic(self, need, snapshot):
            result = super().assign_topic(need, snapshot)
            meta = result.metadata[0]
            changed = {"prompt_hash": "f" * 64} if invalid == "hash" else {"model_id": "other-model"}
            if invalid != "live-authored":
                meta = meta.model_copy(update=changed)
            return result.model_copy(update={"metadata": [meta]})

    adapter = ForgedLegacy()
    adapter.profile.prompt_hashes.pop("propose_topic")
    repo = setup(settings, fixture, 1)
    mode = "live" if invalid == "live-authored" else "replay"
    service = RunService(repo, mode, adapter.adapters())
    try:
        run = wait_run(repo, service.start(RunRequest()).run_id)
        assert run.state == "completed_with_errors"
        assert any(e.code == "ANALYSIS_PROVENANCE_CONFLICT" for e in run.errors)
        assert repo.list_topic_ids(None) == []
    finally:
        service.close()


def test_cold_start_missing_proposal_record_is_visible_and_retry_reuses_extraction(tmp_path):
    profile, _, _, tickets, _ = prepare_proposal_replay(tmp_path, warm=False)
    # Получаем точные первые три запроса на новой пустой synthetic БД.
    settings = Settings(_env_file=None, app_db_path=tmp_path / "record.db", app_model_mode="replay")
    repo = repository_for_seed(settings)
    repo.register_dataset(DatasetRegistration(dataset_id="synthetic-cold-failure", synthetic=True, tickets=tickets[:1]))
    gateway = ProposalFixtureGateway(profile)
    service = RunService(repo, "replay", integration.build_text_pipeline(gateway, profile, settings))
    try:
        assert wait_run(repo, service.start(RunRequest()).run_id).state == "completed"
    finally:
        service.close()
    assert [r.request.operation for r in gateway.records] == ["extract_needs", "assign_topic", "propose_topic"]
    missing = tmp_path / "missing-proposal-recordings"
    complete = tmp_path / "complete-proposal-recordings"
    publish_records(missing, gateway.records[:2])
    publish_records(complete, gateway.records)
    settings = Settings(_env_file=None, app_db_path=tmp_path / "retry.db", app_model_mode="replay")
    repo = repository_for_seed(settings)
    repo.register_dataset(DatasetRegistration(dataset_id="synthetic-cold-failure", synthetic=True, tickets=tickets[:1]))
    service = RunService(repo, "replay", integration.build_text_pipeline(
        ReplayGateway(missing, profile.model_id, profile.parameters["normalization_hash"]), profile, settings))
    try:
        failed = wait_run(repo, service.start(RunRequest()).run_id)
        assert failed.state == "completed_with_errors"
        assert [e.code for e in failed.errors] == ["REPLAY_RECORD_MISSING"]
        assert repo.list_topic_ids(None) == [] and repo.get_source(tickets[0].source_id).needs
        with repo._session() as db:
            assert db.execute("SELECT COUNT(*) FROM stage_checkpoints WHERE stage='assignment'").fetchone()[0] == 0
    finally:
        service.close()
    # Явно подключаем полный набор в тесте; приложение не делает fallback.
    service = RunService(Repository(repo.path), "replay", integration.build_text_pipeline(
        ReplayGateway(complete, profile.model_id, profile.parameters["normalization_hash"]), profile, settings))
    try:
        retried = wait_run(repo, service.start(RunRequest()).run_id)
        assert retried.state == "completed" and retried.reused_steps == 2 and retried.completed_steps == 1
        assert len(repo.list_topic_ids(None)) == 1
    finally:
        service.close()
