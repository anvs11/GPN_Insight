"""Реальный текстовый pipeline/API на новых SQLite и точном authored replay."""

import json
import time
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.db import initialize
from app.ingestion.importer import import_tickets
from app.ingestion.integration import build_text_pipeline, build_text_profile
from app.ingestion.normalizer import NORMALIZATION_HASH, TextNormalizer
from app.main import create_app
from app.replay import ReplayGateway, publish_records
from app.repositories import Repository
from app.runs import RunService
from app.schemas import RunRequest
from scripts.text_pipeline_fixture import AuthoredFixtureGateway, TOPIC_ID, seed_existing_topic

PERIOD = {"date_from": "2026-10-01", "date_to": "2026-10-31"}


def wait_run(repo, run_id):
    limit = time.monotonic() + 10
    while time.monotonic() < limit:
        run = repo.get_run(run_id)
        if run.state not in {"queued", "running"}:
            return run
        time.sleep(.01)
    raise AssertionError("Synthetic text pipeline did not finish in 10 seconds")


def setup_database(settings, profile, manifest, *, warm=True):
    repo = Repository(settings.repo_path(settings.app_db_path))
    initialize(repo.path)
    before = seed_existing_topic(repo, TextNormalizer(profile)) if warm else None
    preview = import_tickets(manifest, dry_run=True, repository=repo)
    assert preview.can_commit and preview.counts.inserted == 4
    result = import_tickets(manifest, dry_run=False, repository=repo)
    assert result.counts.inserted == 4
    return repo, before


def prepare_recordings(folder, *, warm=True):
    settings = Settings(_env_file=None, app_model_mode="replay", app_db_path=folder / "capture.db")
    profile = build_text_profile(settings, model_id="synthetic-text-integration-no-live-model",
        recording_set_version="text-pipeline-authored-v1", parameters={"temperature": 0})
    manifest = (REPO_ROOT / "data/demo/manifest.json").read_bytes()
    repo, _ = setup_database(settings, profile, manifest, warm=warm)
    gateway = AuthoredFixtureGateway(profile)
    service = RunService(repo, "replay", build_text_pipeline(gateway, profile, settings))
    try:
        run = wait_run(repo, service.start(RunRequest(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))).run_id)
        assert run.state == "completed", run.errors
        assert len(gateway.records) == 8
    finally:
        service.close()
    recordings = folder / "recordings"
    publish_records(recordings, gateway.records)
    return profile, manifest, recordings


def check_pipeline(folder):
    folder.mkdir(parents=True)
    profile, manifest, recordings = prepare_recordings(folder)
    settings = Settings(_env_file=None, app_model_mode="replay", app_db_path=folder / "verify.db",
        app_recordings_root=recordings, app_replay_model_id=profile.model_id, app_replay_normalization_hash=NORMALIZATION_HASH)
    repo, before = setup_database(settings, profile, manifest)
    gateway = ReplayGateway(recordings, profile.model_id, NORMALIZATION_HASH)
    adapters = build_text_pipeline(gateway, profile, settings)
    print("synthetic=true; preparation=authored-fixture; verification=strict-replay; models_called=false")
    with TestClient(create_app(settings, pipeline_adapters=adapters), base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert health["pipeline_ready"] and health["available_models"]["text"]["code"] == "REPLAY_READY"
        response = client.post("/api/v1/runs", json=PERIOD)
        assert response.status_code == 202
        run = wait_run(repo, response.json()["run_id"])
        assert run.state == "completed" and run.origins == ["authored"] and run.completed_steps == 12
        assert client.get("/api/v1/runs/" + run.run_id).json()["state"] == "completed"
        card = repo.get_topic(TOPIC_ID, "replay")
        assert card.stats.ticket_count == 5 and card.history == before.history
        assert card.topic.status == before.topic.status and card.topic.owner == before.topic.owner
        for ticket in json.loads(manifest)["tickets"]:
            source = client.get("/api/v1/sources/" + ticket["source_id"])
            assert source.status_code == 200 and source.json()["ticket"]["text"] == ticket["text"]
            assert source.json()["document"]["fragments"][0]["warning"]
        saved = client.patch("/api/v1/topics/" + TOPIC_ID + "/decision", json={
            "status": "needs_research", "owner": "Проверяющий", "reason": "Синтетическая проверка после анализа", "version": card.topic.version})
        assert saved.status_code == 200
        saved_card = repo.get_topic(TOPIC_ID, "replay")
        assert len(saved_card.history) == len(before.history) + 1
        print("api: 202 -> completed; actual_normalizer=true; actual_analysis_service=true; origins=authored")
        print("warm-match: imported=4; ticket_count=5 (including one authored seed); decision/history=preserved")
        repeat_import = import_tickets(manifest, dry_run=False, repository=repo)
        assert repeat_import.counts.unchanged == 4
        repeated = client.post("/api/v1/runs", json=PERIOD)
        assert repeated.status_code == 202
        repeat = wait_run(repo, repeated.json()["run_id"])
        assert repeat.state == "completed" and repeat.reused_steps == 12 and repeat.completed_steps == 0
        assert repo.get_topic(TOPIC_ID, "replay") == saved_card
        print("repeat: unchanged=4; reused_steps=12; completed_steps=0; count/decision/history=unchanged")
        changed = json.loads(manifest)
        changed["tickets"][0]["text"] += " Синтетическое обновление обращения."
        delta = import_tickets(json.dumps(changed, ensure_ascii=False), dry_run=False, repository=repo)
        assert delta.counts.updated == 1 and delta.counts.unchanged == 3
        missing = client.post("/api/v1/runs", json=PERIOD)
        assert missing.status_code == 202
        failed = wait_run(repo, missing.json()["run_id"])
        assert failed.state == "completed_with_errors" and any(e.code == "REPLAY_RECORD_MISSING" for e in failed.errors)
        assert repo.get_source("PREVIEW-T001").requires_analysis
        assert repo.get_source("PREVIEW-T001", version=1).needs
        after = repo.get_topic(TOPIC_ID, "replay")
        assert after.topic.topic_id == saved_card.topic.topic_id and after.topic.owner == saved_card.topic.owner
        assert after.topic.status == saved_card.topic.status and after.history == saved_card.history
        assert after.stats.ticket_count == 4 and after.pending_source_ids == ["PREVIEW-T001"]
        print("delta: updated=1 unchanged=3; unknown replay=REPLAY_RECORD_MISSING; empty_success=false")
    reopened = Repository(repo.path)
    assert reopened.get_topic(TOPIC_ID, "replay") == after
    print("reopen: source_history/topic_id/decision/history=preserved; active_count=4; pending=1")
    report = {"synthetic": True, "origin": "authored", "models_called": False,
        "real_components": ["TextNormalizer", "AnalysisService", "ReplayGateway", "Pipeline", "RunService", "FastAPI"],
        "normalization_hash": NORMALIZATION_HASH, "first_state": run.state, "first_completed_steps": run.completed_steps,
        "repeat_reused_steps": repeat.reused_steps, "delta_state": failed.state, "delta_error": "REPLAY_RECORD_MISSING",
        "decision_history_preserved_after_reopen": True, "active_ticket_count_after_delta": 4, "pending_count": 1}
    (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: real text pipeline/API and strict replay; no models or working database")
    print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    return report


if __name__ == "__main__":
    check_pipeline(REPO_ROOT / "var" / ("text-pipeline-check-" + uuid4().hex))
