import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import MAX_PROFILE_BYTES
from app.config import Settings
from app.ingestion.normalizer import NORMALIZATION_HASH
from app.main import create_app
from app.replay import ReplayGateway, make_record, publish_records
from app.schemas import BatchAssignment, ExtractedNeeds
from scripts.check_text_pipeline import PERIOD, prepare_recordings, setup_database, wait_run
from scripts.text_pipeline_fixture import AuthoredFixtureGateway, TOPIC_ID


@pytest.fixture
def configured(tmp_path):
    folder = tmp_path / "configured-runtime"
    folder.mkdir()
    profile, manifest, recordings = prepare_recordings(folder)
    profile_path = folder / "profile.json"
    profile_path.write_text(profile.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, app_db_path=folder / "target.db", app_model_mode="replay",
        app_recordings_root=recordings, app_replay_model_id=profile.model_id,
        app_replay_normalization_hash=NORMALIZATION_HASH, app_processing_profile_path=profile_path)
    repo, before = setup_database(settings, profile, manifest)
    return SimpleNamespace(folder=folder, profile=profile, manifest=manifest, settings=settings,
        recordings=recordings, path=profile_path, repo=repo, before=before)


def test_normal_create_app_binds_actual_gateway_and_reuses_checkpoints(configured, monkeypatch):
    c = configured
    monkeypatch.setattr(AuthoredFixtureGateway, "generate", lambda *a, **k: pytest.fail("Fixture gateway reached ordinary server"))
    app = create_app(c.settings)
    assert type(app.state.text_gateway) is ReplayGateway
    adapters = app.state.run_service.adapters
    assert adapters.profile == c.profile
    assert adapters.analysis.service.gateway.gateway is app.state.text_gateway
    before_files = {p: sha256(p.read_bytes()).hexdigest() for p in c.recordings.rglob("*") if p.is_file()}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert health["pipeline_ready"] and health["integration_errors"] == []
        assert health["model_mode"] == "replay" and health["replay_origins"] == ["authored"]
        first = client.post("/api/v1/runs", json=PERIOD)
        assert first.status_code == 202
        run = wait_run(c.repo, first.json()["run_id"])
        assert run.state == "completed" and run.origins == ["authored"]
        repeat = client.post("/api/v1/runs", json=PERIOD)
        repeated = wait_run(c.repo, repeat.json()["run_id"])
        assert repeated.state == "completed" and repeated.reused_steps == 12 and repeated.completed_steps == 0
        card = c.repo.get_topic(TOPIC_ID, "replay")
        assert card.stats.ticket_count == 5 and card.history == c.before.history
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        reopened = client.post("/api/v1/runs", json=PERIOD)
        assert wait_run(c.repo, reopened.json()["run_id"]).reused_steps == 12
        assert c.repo.get_topic(TOPIC_ID, "replay") == card
    assert c.path.read_text(encoding="utf-8") == c.profile.model_dump_json()
    assert all(sha256(p.read_bytes()).hexdigest() == digest for p, digest in before_files.items())


@pytest.mark.parametrize("kind,code", [
    ("missing", "PROCESSING_PROFILE_UNAVAILABLE"), ("json", "PROCESSING_PROFILE_INVALID"),
    ("duplicate", "PROCESSING_PROFILE_INVALID"), ("oversize", "PROCESSING_PROFILE_TOO_LARGE"),
    ("unicode", "PROCESSING_PROFILE_INVALID"), ("nan", "PROCESSING_PROFILE_INVALID"),
    ("prompt", "PROCESSING_PROFILE_CONFLICT"), ("normalization", "PROCESSING_PROFILE_CONFLICT"),
    ("version", "PROCESSING_PROFILE_CONFLICT"), ("settings", "PROCESSING_PROFILE_CONFLICT"),
    ("bool-number", "PROCESSING_PROFILE_CONFLICT"), ("model", "REPLAY_PROFILE_CONFLICT"),
    ("normalization-setting", "REPLAY_PROFILE_CONFLICT"), ("set-version", "REPLAY_PROFILE_CONFLICT"),
    ("parameters", "REPLAY_PROFILE_CONFLICT"), ("recordings-missing", "REPLAY_SET_INVALID"),
    ("missing-id", "REPLAY_NOT_CONFIGURED"), ("missing-normalization", "REPLAY_NOT_CONFIGURED"),
])
def test_configuration_conflict_stays_unready_without_rewriting(configured, kind, code, caplog):
    c = configured
    data = c.profile.model_dump(mode="json")
    if kind == "missing": c.settings.app_processing_profile_path = c.folder / "missing-profile.json"
    elif kind == "json": c.path.write_text('{"SENSITIVE_SENTINEL":', encoding="utf-8")
    elif kind == "duplicate": c.path.write_text('{"model_id":"SENSITIVE_SENTINEL","model_id":"other"}', encoding="utf-8")
    elif kind == "oversize": c.path.write_bytes(b"x" * (MAX_PROFILE_BYTES + 1))
    elif kind == "unicode": c.path.write_bytes(b"\xffSENSITIVE_SENTINEL")
    elif kind == "nan": c.path.write_text('{"SENSITIVE_SENTINEL":NaN}', encoding="utf-8")
    elif kind == "prompt": data["prompt_hashes"]["extract_needs"] = "f" * 64
    elif kind == "normalization": data["parameters"]["normalization_hash"] = "f" * 64
    elif kind == "version": data["normalization_version"] = "old"
    elif kind == "settings": c.settings.app_retrieval_k = 1
    elif kind == "bool-number": data["parameters"]["text_integration"]["retrieval_k"] = 20.0
    elif kind == "model": c.settings.app_replay_model_id = "other-configured-model"
    elif kind == "normalization-setting": c.settings.app_replay_normalization_hash = "f" * 64
    elif kind == "set-version": data["recording_set_version"] = "other-version"
    elif kind == "parameters": data["parameters"]["temperature"] = False  # JSON false != 0
    elif kind == "recordings-missing": c.settings.app_recordings_root = c.folder / "missing-recordings"
    elif kind == "missing-id": c.settings.app_replay_model_id = ""
    elif kind == "missing-normalization": c.settings.app_replay_normalization_hash = ""
    if kind in {"prompt", "normalization", "version", "bool-number", "set-version", "parameters"}:
        c.path.write_text(json.dumps(data), encoding="utf-8")
    before = {p: p.read_bytes() for p in c.recordings.rglob("*") if p.is_file()}
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health")
        assert health.json()["pipeline_ready"] is False and code in health.json()["integration_errors"]
        response = client.post("/api/v1/runs", json=PERIOD)
        assert response.status_code == 503 and response.json()["code"] == "INTEGRATION_NOT_READY"
        assert "SENSITIVE_SENTINEL" not in response.text + health.text + caplog.text
        assert c.repo.active_run() is None
    assert all(p.read_bytes() == content for p, content in before.items())


def test_actual_live_factory_remains_unavailable_even_with_profile(configured):
    c = configured
    c.settings.app_model_mode = "live"
    c.settings.text_model_id = "synthetic-configured-id-not-a-live-model"
    c.settings.text_model_base_url = "https://internal.invalid"
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert health["model_mode"] == "live" and health["pipeline_ready"] is False
        assert "MODEL_ADAPTER_NOT_IMPLEMENTED" in health["integration_errors"]
        assert client.post("/api/v1/runs", json=PERIOD).status_code == 503


def test_profile_is_never_inferred_from_records_when_path_is_absent(configured):
    c = configured
    c.settings.app_processing_profile_path = None
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert health["available_models"]["text"]["code"] == "REPLAY_READY"
        assert health["pipeline_ready"] is False
        assert "PROCESSING_PROFILE_NOT_CONFIGURED" in health["integration_errors"]
        assert client.post("/api/v1/runs", json=PERIOD).status_code == 503


def test_server_profile_utf8_bom_is_supported_without_rewriting(configured):
    c = configured
    content = b"\xef\xbb\xbf" + c.profile.model_dump_json().encode("utf-8")
    c.path.write_bytes(content)
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/health").json()["pipeline_ready"] is True
    assert c.path.read_bytes() == content


def test_running_server_mode_cannot_be_relabelled_by_mutating_callers_settings(configured):
    c = configured
    app = create_app(c.settings)
    c.settings.app_model_mode = "live"
    c.settings.app_processing_profile_path = c.folder / "not-the-running-profile.json"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert health["model_mode"] == "replay" and health["pipeline_ready"] is True
        response = client.post("/api/v1/runs", json=PERIOD)
        run = wait_run(c.repo, response.json()["run_id"])
        assert run.mode == "replay" and run.origins == ["authored"] and run.state == "completed"


def test_unknown_replay_input_is_a_run_error_not_empty_success(configured):
    c = configured
    data = json.loads(c.manifest)
    data["tickets"][0]["text"] += " Синтетический неизвестный вход."
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        changed = client.post("/api/v1/imports", files={"manifest": ("manifest.json", json.dumps(data).encode(), "application/json")})
        assert changed.status_code == 200 and changed.json()["counts"]["updated"] == 1
        response = client.post("/api/v1/runs", json=PERIOD)
        run = wait_run(c.repo, response.json()["run_id"])
        assert run.state == "completed_with_errors" and any(e.code == "REPLAY_RECORD_MISSING" for e in run.errors)
        assert c.repo.get_source("PREVIEW-T001").requires_analysis
        assert c.repo.get_source("PREVIEW-T001", version=1).ticket.text != data["tickets"][0]["text"]
        assert c.repo.get_topic(TOPIC_ID, "replay").history == c.before.history


def test_active_recording_version_change_preserves_provenance_failure(configured):
    c = configured
    app = create_app(c.settings)
    old_files = {p: p.read_bytes() for p in c.recordings.rglob("sets/*/*.json")}
    assert len(old_files) == 9
    records = ReplayGateway(c.recordings, c.profile.model_id, NORMALIZATION_HASH).records()
    changed = []
    for record in records.values():
        schema = ExtractedNeeds if record.request.operation == "extract_needs" else BatchAssignment
        changed.append(make_record(record.request, schema, record.response, model_id=c.profile.model_id,
            normalization_hash=NORMALIZATION_HASH, set_version="synthetic-new-version", origin="authored"))
    publish_records(c.recordings, changed)  # Новая версия, старые sets не переписываются.
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/runs", json=PERIOD)
        run = wait_run(c.repo, response.json()["run_id"])
        assert run.state == "completed_with_errors" and any(e.code == "ANALYSIS_PROVENANCE_CONFLICT" for e in run.errors)
        assert c.repo.get_topic(TOPIC_ID, "replay").history == c.before.history
    assert all(p.read_bytes() == content for p, content in old_files.items())
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        assert "REPLAY_PROFILE_CONFLICT" in client.get("/api/v1/health").json()["integration_errors"]


def test_configured_runtime_material_error_never_calls_gateway(configured, monkeypatch):
    c = configured
    import app.ingestion.normalizer as normalizer
    mask = normalizer.mask_pii
    def broken(text):
        if "старый контактный телефон" in text:
            raise RuntimeError("SENSITIVE_SENTINEL")
        return mask(text)
    monkeypatch.setattr(normalizer, "mask_pii", broken)
    calls = []
    generate = ReplayGateway.generate
    def observed(self, request, response_schema):
        calls.append(request)
        return generate(self, request, response_schema)
    monkeypatch.setattr(ReplayGateway, "generate", observed)
    with TestClient(create_app(c.settings), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/runs", json=PERIOD)
        run = wait_run(c.repo, response.json()["run_id"])
        assert run.state == "completed_with_errors" and any(e.code == "EXTRACTION_MATERIAL_ERRORS" for e in run.errors)
        assert not any(r.operation == "extract_needs" and r.data["document"]["source_id"] == "PREVIEW-T001" for r in calls)
        assert "SENSITIVE_SENTINEL" not in run.model_dump_json()
        assert c.repo.get_topic(TOPIC_ID, "replay").history == c.before.history
