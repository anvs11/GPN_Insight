"""Только проверка: синтетические адаптеры tests, отдельная БД, localhost, без моделей."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import REPO_ROOT
from app.db import initialize
from app.demo import load_fixture
from app.repositories import Repository
from app.schemas import DatasetRegistration


def main():
    folder = REPO_ROOT / "var" / ("pipeline-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    path = folder / "check.db"
    initialize(path)
    repo = Repository(path)
    fixture = load_fixture(REPO_ROOT / "data/contracts/demo_state.json")
    repo.register_dataset(DatasetRegistration(dataset_id="pipeline-check", synthetic=True, tickets=list(reversed(fixture.sources[:2]))))
    harness = folder / "pipeline_harness.py"
    harness.write_text('''import os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["CHECK_BACKEND"])
sys.path.insert(0, os.environ["CHECK_TESTS"])
from app.main import create_app
from app.config import Settings
from pipeline_adapters import TestComponents
class Components(TestComponents):
    def assign_topic(self, need, topic_passports):
        folder=Path(os.environ["CHECK_FOLDER"])
        if os.environ.get("CHECK_BLOCK") == "1":
            (folder/"assignment-entered").write_text("test-only")
            limit=time.monotonic()+30
            while time.monotonic()<limit:
                time.sleep(.05)
            raise RuntimeError("Test process should have been interrupted")
        return super().assign_topic(need,topic_passports)
app=create_app(Settings(),pipeline_adapters=Components().adapters() if os.environ.get("CHECK_ADAPTERS")=="1" else None)
''', encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, APP_DB_PATH=str(path), APP_MODEL_MODE="replay", PYTHONUTF8="1",
        CHECK_BACKEND=str(REPO_ROOT / "backend"), CHECK_TESTS=str(REPO_ROOT / "backend/tests"), CHECK_FOLDER=str(folder))
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=2, trust_env=False)
    process = None

    def start(block, *, adapters=True):
        child_env = dict(env, CHECK_BLOCK="1" if block else "0", CHECK_ADAPTERS="1" if adapters else "0")
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", "pipeline_harness:app", "--app-dir", str(folder),
            "--host", "127.0.0.1", "--port", str(port), "--workers", "1"], cwd=REPO_ROOT / "backend", env=child_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for _ in range(100):
            try:
                if client.get("/health").status_code == 200: return child
            except httpx.TransportError: pass
            if child.poll() is not None: raise RuntimeError("Test Uvicorn exited")
            time.sleep(.05)
        child.terminate()
        child.wait(timeout=5)
        raise RuntimeError("Test Uvicorn startup timeout")

    def wait_run(run_id):
        for _ in range(200):
            current = client.get("/runs/" + run_id).json()
            if current["state"] not in {"queued", "running"}: return current
            time.sleep(.02)
        raise RuntimeError("Test run timeout")

    print("synthetic=true; adapters=tests-only; model_mode=replay; no live model calls")
    try:
        process = start(False, adapters=False)
        assert client.get("/health").json()["pipeline_ready"] is False
        response = client.post("/runs", json={})
        assert response.status_code == 503 and response.json()["code"] == "INTEGRATION_NOT_READY"
        assert repo.active_run() is None
        assert client.get("/runs/missing").status_code == 404
        print("PASS: default app refuses unconnected analysis with 503; no fake run")
        process.terminate()
        process.wait(timeout=5)
        process = start(True)
        first = client.post("/runs", json={})
        assert first.status_code == 202
        run_id = first.json()["run_id"]
        for _ in range(100):
            if (folder / "assignment-entered").exists(): break
            time.sleep(.02)
        assert (folder / "assignment-entered").exists()
        assert client.post("/runs", json={}).json()["run_id"] == run_id
        assert client.get("/health").json()["processing_state"] == "busy"
        print("PASS: POST 202; repeated click returns active run; extraction pass finished before assignment")
        # Прерываем только собственный проверочный процесс, имитируя аварийный выход.
        process.kill()
        process.wait(timeout=5)
        process = start(False)
        assert client.get("/runs/" + run_id).json()["state"] == "interrupted"
        resumed = client.post("/runs", json={}).json()
        result = wait_run(resumed["run_id"])
        assert result["state"] == "completed" and result["reused_steps"] == 4 and result["completed_steps"] == 2
        assert result["origins"] == ["authored"]
        print("PASS: actual process restart marks interrupted; four successful steps reused; two assignments committed")
        with repo.transaction() as tx:
            topic_id = tx.list_topic_ids(None)[0]
        card = client.get("/topics/" + topic_id).json()
        body = json.loads((REPO_ROOT / "data/contracts/decision.json").read_text(encoding="utf-8"))
        body["version"] = card["topic"]["version"]
        saved = client.patch("/topics/" + topic_id + "/decision", json=body).json()
        repeated = wait_run(client.post("/runs", json={}).json()["run_id"])
        assert repeated["state"] == "completed" and repeated["reused_steps"] == 6
        assert client.get("/topics/" + topic_id).json() == saved
        assert len(repo.list_topic_ids(None)) == 2
        print("PASS: completed repeat reuses six steps; stable topics/counts and decision/history preserved")
        report = {"synthetic": True, "test_adapters_only": True, "model_quality_measured": False,
            "restart": "interrupted", "resumed_steps": 4, "repeated_steps": 6,
            "decision_preserved": True, "topics": 2, "live_integration": "not_connected"}
        (folder / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("report: " + str((folder / "result.json").relative_to(REPO_ROOT)))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        client.close()


if __name__ == "__main__":
    main()
