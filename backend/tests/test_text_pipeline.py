import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion.integration import build_text_pipeline
from app.ingestion.normalizer import NORMALIZATION_HASH
from app.main import create_app
from app.replay import ReplayGateway
from app.repositories import Repository
from scripts.check_text_pipeline import PERIOD, check_pipeline, prepare_recordings, setup_database, wait_run
from scripts.text_pipeline_fixture import TOPIC_ID


def test_real_text_pipeline_import_api_decision_repeat_and_delta(tmp_path):
    report = check_pipeline(tmp_path / "full-path")
    assert report["first_state"] == "completed" and report["repeat_reused_steps"] == 12
    assert report["delta_state"] == "completed_with_errors" and report["models_called"] is False


def test_real_cold_start_creates_first_topic_and_matches_remaining_sources(tmp_path):
    folder = tmp_path / "cold"
    folder.mkdir()
    profile, manifest, recordings = prepare_recordings(folder, warm=False)
    settings = Settings(_env_file=None, app_model_mode="replay", app_db_path=folder / "target.db")
    repo, _ = setup_database(settings, profile, manifest, warm=False)
    adapters = build_text_pipeline(ReplayGateway(recordings, profile.model_id, NORMALIZATION_HASH), profile, settings)
    with TestClient(create_app(settings, pipeline_adapters=adapters), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/runs", json=PERIOD)
        assert response.status_code == 202
        run = wait_run(repo, response.json()["run_id"])
        assert run.state == "completed" and run.origins == ["authored"]
        topic_ids = repo.list_topic_ids(None)
        assert len(topic_ids) == 1
        card = client.get("/api/v1/topics/" + topic_ids[0]).json()
        assert card["stats"]["ticket_count"] == 4 and card["data_origin"] == "authored"
        assert card["topic"]["status"] == "new" and card["topic"]["owner"] is None
    with sqlite3.connect(repo.path) as db:
        assert db.execute("SELECT count(*) FROM review_records").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM stored_needs").fetchone()[0] == 4


def test_real_pipeline_resume_after_gateway_failure_preserves_completed_steps(tmp_path):
    folder = tmp_path / "resume"
    folder.mkdir()
    profile, manifest, recordings = prepare_recordings(folder)
    settings = Settings(_env_file=None, app_db_path=folder / "target.db", app_model_mode="replay")
    repo, before = setup_database(settings, profile, manifest)

    class FaultOnceReplay(ReplayGateway):
        fail = True

        def generate(self, request, response_schema):
            if self.fail and request.operation == "extract_needs" and request.data["document"]["source_id"] == "PREVIEW-T001":
                raise TimeoutError("Synthetic transport interruption")
            return super().generate(request, response_schema)

    gateway = FaultOnceReplay(recordings, profile.model_id, NORMALIZATION_HASH)
    with TestClient(create_app(settings, pipeline_adapters=build_text_pipeline(gateway, profile, settings)), base_url="http://127.0.0.1") as client:
        first = client.post("/api/v1/runs", json=PERIOD)
        failed = wait_run(repo, first.json()["run_id"])
        assert failed.state == "completed_with_errors" and any(e.code == "ANALYSIS_TIMEOUT" for e in failed.errors)
        gateway.fail = False
        second = client.post("/api/v1/runs", json=PERIOD)
        resumed = wait_run(repo, second.json()["run_id"])
        assert resumed.state == "completed" and resumed.reused_steps >= 7 and resumed.errors == []
        after = repo.get_topic(TOPIC_ID, "replay")
        assert after.topic.status == before.topic.status and after.history == before.history
        assert after.stats.ticket_count == 5
    assert Repository(repo.path).get_topic(TOPIC_ID, "replay") == after


def test_normalization_error_never_calls_extraction_or_publishes_empty_success(tmp_path, monkeypatch):
    folder = tmp_path / "normalization-failure"
    folder.mkdir()
    profile, manifest, recordings = prepare_recordings(folder)
    settings = Settings(_env_file=None, app_db_path=folder / "target.db", app_model_mode="replay")
    repo, before = setup_database(settings, profile, manifest)
    import app.ingestion.normalizer as module
    original_mask = module.mask_pii

    def failing_mask(text):
        if "старый контактный телефон" in text:
            raise RuntimeError("SENSITIVE_SENTINEL")
        return original_mask(text)

    class ObservedReplay(ReplayGateway):
        requests = []

        def generate(self, request, response_schema):
            self.requests.append(request)
            return super().generate(request, response_schema)

    monkeypatch.setattr(module, "mask_pii", failing_mask)
    gateway = ObservedReplay(recordings, profile.model_id, NORMALIZATION_HASH)
    with TestClient(create_app(settings, pipeline_adapters=build_text_pipeline(gateway, profile, settings)), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/runs", json=PERIOD)
        run = wait_run(repo, response.json()["run_id"])
        assert run.state == "completed_with_errors"
        assert any(e.code == "NORMALIZATION_MASKING_FAILED" for e in run.errors)
        assert any(e.code == "EXTRACTION_MATERIAL_ERRORS" for e in run.errors)
        assert all(r.data["document"]["source_id"] != "PREVIEW-T001" for r in gateway.requests if r.operation == "extract_needs")
        assert "SENSITIVE_SENTINEL" not in run.model_dump_json()
        source = repo.get_source("PREVIEW-T001")
        assert source.needs == [] and source.document.errors and source.requires_analysis
        assert repo.get_topic(TOPIC_ID, "replay").history == before.history
