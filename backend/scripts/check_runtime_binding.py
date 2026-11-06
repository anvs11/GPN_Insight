"""Обычный app.main:app с изолированной, явно synthetic/authored replay-конфигурацией."""

import json
import os
import socket
import subprocess
import sys
import time
from hashlib import sha256
from uuid import uuid4

import httpx

from app.config import REPO_ROOT
from app.db import initialize
from app.ingestion.normalizer import NORMALIZATION_HASH, TextNormalizer
from app.repositories import Repository
from scripts.check_text_pipeline import PERIOD, prepare_recordings, wait_run
from scripts.text_pipeline_fixture import TOPIC_ID, seed_existing_topic


def main():
    folder = REPO_ROOT / "var" / ("runtime-binding-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    # Только отдельная подготовка явно authored-ответов. Дочерний обычный
    # сервер не импортирует fixture gateway и не получает PipelineAdapters.
    profile, manifest, recordings = prepare_recordings(folder)
    profile_path = folder / "processing-profile.json"
    with profile_path.open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json())
    immutable = {p: sha256(p.read_bytes()).hexdigest() for p in recordings.rglob("*") if p.is_file()}
    immutable[profile_path] = sha256(profile_path.read_bytes()).hexdigest()
    path = folder / "server.db"
    initialize(path)
    repo = Repository(path)
    before = seed_existing_topic(repo, TextNormalizer(profile))
    policy = profile.parameters["text_integration"]
    env = dict(os.environ, APP_DB_PATH=str(path), APP_PROCESSING_PROFILE_PATH=str(profile_path), APP_MODEL_MODE="replay",
        APP_RECORDINGS_ROOT=str(recordings), APP_REPLAY_MODEL_ID=profile.model_id,
        APP_REPLAY_NORMALIZATION_HASH=NORMALIZATION_HASH, APP_P_MAX=str(policy["p_max"]),
        APP_RETRIEVAL_K=str(policy["retrieval_k"]), APP_PASSPORT_BATCH_MAX_CHARS=str(policy["passport_batch_max_chars"]),
        APP_MAX_ASSIGN_BATCHES=str(policy["max_assign_batches"]), PYTHONUTF8="1",
        TEXT_MODEL_BASE_URL="", TEXT_MODEL_ID="", ASR_BASE_URL="", VL_BASE_URL="")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=10, trust_env=False)
    process = None

    def start(mode="replay"):
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"],
            cwd=REPO_ROOT / "backend", env={**env, "APP_MODEL_MODE": mode}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for _ in range(100):
            try:
                if client.get("/health").status_code == 200:
                    return child
            except httpx.TransportError:
                pass
            if child.poll() is not None:
                raise RuntimeError("Configured ordinary server exited")
            time.sleep(.05)
        child.terminate()
        child.wait(timeout=5)
        raise RuntimeError("Configured server startup timeout")

    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    def run():
        response = client.post("/runs", json=PERIOD)
        assert response.status_code == 202
        return wait_run(repo, response.json()["run_id"])

    try:
        process = start()
        health = client.get("/health").json()
        assert health["pipeline_ready"] and health["model_mode"] == "replay" and health["replay_origins"] == ["authored"]
        print("configuration=isolated-synthetic; server=app.main:app; mode=replay; origins=authored; models_called=false")
        print("binding: configured-profile=exact; gateway=ReplayGateway; fixture_injection=false")
        imported = client.post("/imports", files={"manifest": ("manifest.json", manifest, "application/json")})
        assert imported.status_code == 200 and imported.json()["counts"]["inserted"] == 4
        first = run()
        assert first.state == "completed" and first.origins == ["authored"]
        card = client.get("/topics/" + TOPIC_ID).json()
        assert card["stats"]["ticket_count"] == 5 and repo.get_topic(TOPIC_ID, "replay").history == before.history
        saved = client.patch("/topics/" + TOPIC_ID + "/decision", json={"status": "needs_research", "owner": "Проверяющий",
            "reason": "Синтетическая проверка обычной сборки", "version": card["topic"]["version"]})
        assert saved.status_code == 200
        saved_card = saved.json()
        for item in json.loads(manifest)["tickets"]:
            source = client.get("/sources/" + item["source_id"])
            assert source.status_code == 200 and source.json()["document"]["fragments"][0]["warning"]
            assert source.json()["ticket"]["text"] == item["text"]
        print("api: inserted=4; run=202->completed; source/decision/history=preserved")
        repeated_import = client.post("/imports", files={"manifest": ("manifest.json", manifest, "application/json")})
        assert repeated_import.json()["counts"]["unchanged"] == 4
        repeat = run()
        assert repeat.state == "completed" and repeat.reused_steps == 12 and repeat.completed_steps == 0
        stop(process)
        process = None
        process = start()
        reopened = run()
        assert reopened.state == "completed" and reopened.reused_steps == 12
        assert client.get("/topics/" + TOPIC_ID).json() == saved_card
        print("repeat/restart: unchanged=4; reused_steps=12; decision/history/checkpoints=preserved")
        delta = json.loads(manifest)
        delta["tickets"][0]["text"] += " Синтетический неизвестный для replay текст."
        updated = client.post("/imports", files={"manifest": ("manifest.json", json.dumps(delta, ensure_ascii=False).encode(), "application/json")})
        assert updated.status_code == 200 and updated.json()["counts"]["updated"] == 1
        unknown = run()
        assert unknown.state == "completed_with_errors" and any(e.code == "REPLAY_RECORD_MISSING" for e in unknown.errors)
        after = client.get("/topics/" + TOPIC_ID).json()
        assert after["history"] == saved_card["history"] and after["topic"]["owner"] == saved_card["topic"]["owner"]
        assert after["pending_source_ids"] == ["PREVIEW-T001"]
        assert repo.get_source("PREVIEW-T001", version=1).needs and repo.get_source("PREVIEW-T001").requires_analysis
        print("unknown: REPLAY_RECORD_MISSING; empty_success=false; old_source/decision/history=preserved")
        stop(process)
        process = None
        process = start("live")
        live = client.get("/health").json()
        assert live["model_mode"] == "live" and live["pipeline_ready"] is False
        assert "MODEL_ADAPTER_NOT_IMPLEMENTED" in live["integration_errors"]
        assert client.post("/runs", json=PERIOD).status_code == 503
        print("live: unavailable MODEL_ADAPTER_NOT_IMPLEMENTED; replay_fallback=false")
        assert all(sha256(p.read_bytes()).hexdigest() == digest for p, digest in immutable.items())
        report = {"synthetic": True, "ordinary_main": True, "explicit_pipeline_adapters": False,
            "mode": "replay", "origins": ["authored"], "models_called": False, "production_configuration_supplied": False,
            "first_state": first.state, "repeated_steps": repeat.reused_steps, "restarted_steps": reopened.reused_steps,
            "unknown_error": "REPLAY_RECORD_MISSING", "live_ready": False,
            "profile_recordings_unchanged": True, "manual_history_and_checkpoints_preserved": True}
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: ordinary server binding, exact replay and honest live unavailability; production config unchanged")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        if process is not None:
            stop(process)
        client.close()


if __name__ == "__main__":
    main()
