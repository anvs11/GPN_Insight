"""Ручная приёмка новых HTTP/CLI на отдельной synthetic SQLite, без моделей."""

import json
import os
import socket
import subprocess
import sys
import time
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from app.config import REPO_ROOT
from app.db import initialize
from app.demo import load_fixture
from app.repositories import Repository
from app.schemas import DecisionUpdate


def main():
    folder = REPO_ROOT / "var" / ("server-completion-check-" + uuid4().hex)
    folder.mkdir()
    private = [REPO_ROOT / ".env", *sorted((REPO_ROOT / "var").glob("*.db"))]
    hashes = {p: sha256(p.read_bytes()).hexdigest() for p in private if p.is_file()}
    fixture = load_fixture(REPO_ROOT / "data/contracts/demo_state.json")
    path = folder / "http.db"
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    topic_id = fixture.topic.topic_id
    repo.record_decision(topic_id, DecisionUpdate(version=1, status="needs_research",
        owner="Синтетический владелец", reason="Сохранить решение при правке паспорта"))
    env = dict(os.environ, APP_DB_PATH=str(path), APP_MODEL_MODE="replay", PYTHONUTF8="1",
        APP_PROCESSING_PROFILE_PATH=str(folder / "intentionally-unconfigured-profile.json"),
        APP_RECORDINGS_ROOT=str(folder / "no-model-recordings"), APP_REPLAY_MODEL_ID="", APP_REPLAY_NORMALIZATION_HASH="",
        TEXT_MODEL_BASE_URL="", TEXT_MODEL_ID="", ASR_BASE_URL="", VL_BASE_URL="")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=10, trust_env=False)
    process = None

    def start():
        child = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"],
            cwd=REPO_ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for _ in range(100):
            try:
                if client.get("/health").status_code == 200:
                    return child
            except httpx.TransportError:
                pass
            if child.poll() is not None:
                raise RuntimeError("Isolated ordinary server exited")
            time.sleep(.05)
        stop(child)
        raise RuntimeError("Isolated server startup timeout")

    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    def cli(*args):
        return subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", *args],
            cwd=REPO_ROOT / "backend", env=env, capture_output=True, timeout=45)

    try:
        process = start()
        print("synthetic=true; transport=localhost-http; server=app.main:app; live-models=none")
        assert client.get("/health").json()["pipeline_ready"] is False
        assert len(client.get("/topics").json()) == 1
        day = fixture.sources[0].created_at.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
        response = client.get("/topics", params=dict(product_id="webneft", support_level="all", status="needs_research",
            date_from=day, date_to=day, review_due="false"))
        assert response.status_code == 200 and len(response.json()) == 1
        selected = response.json()[0]
        assert selected["stats"]["ticket_count"] == 4 and selected["period_stats"]["ticket_count"] >= 1
        assert client.get("/topics?date_from=2099-01-01").json() == []
        assert client.get("/topics?status=new&status=declined").status_code == 422
        print("filters: combined=200; all-time=4; separate MSK period; empty=[]; duplicate-filter=422")
        before = client.get("/topics/" + topic_id).json()
        body = dict(version=before["topic"]["version"], passport_version=before["topic"]["passport"]["version"],
            author="Синтетический аналитик", reason="Уточнены проверенные границы", title="Уточнённая синтетическая тема",
            passport={k: v for k, v in before["topic"]["passport"].items() if k not in {"product_id", "version"}})
        body["passport"]["scope_confirmed"] = True
        saved = client.patch("/topics/" + topic_id + "/passport", json=body)
        assert saved.status_code == 200
        card = saved.json()
        assert card["topic"]["version"] == 3 and card["topic"]["passport"]["version"] == 2
        assert card["topic"]["owner"] == before["topic"]["owner"] and card["history"] == before["history"]
        assert card["stats"] == before["stats"] and card["topic"]["need_ids"] == before["topic"]["need_ids"]
        assert client.patch("/topics/" + topic_id + "/passport", json=body).status_code == 409
        changes = client.get("/topics/" + topic_id + "/changes").json()
        assert len(changes) == 1 and changes[0]["kind"] == "passport"
        print("passport: PATCH=200; topic=3/passport=2; stale=409; decision/history/count/ID preserved")
        stop(process)
        process = None
        process = start()
        assert client.get("/topics/" + topic_id).json() == card
        print("restart: confirmed passport, title, owner and history preserved")
        stop(process)
        process = None
        before_cli = sha256(path.read_bytes()).hexdigest()
        spec = REPO_ROOT / "data/scenarios/text_flow.json"
        recordings = folder / "scenarios"
        captured = cli("record", "--scenario", str(spec), "--authored-scenario", "--recordings", str(recordings))
        assert captured.returncode == 0, captured.stderr.decode("utf-8")
        recorded = json.loads(captured.stdout)
        checked = cli("verify-recordings", "--scenario", str(spec), "--recordings", str(recordings))
        assert checked.returncode == 0, checked.stderr.decode("utf-8")
        verified = json.loads(checked.stdout)
        assert recorded["trace_hash"] == verified["trace_hash"] and verified["steps"] == 9 and verified["record_count"] == 11
        pointer = (recordings / "active.json").read_bytes()
        live = cli("record", "--scenario", str(spec), "--recordings", str(recordings))
        assert live.returncode == 1 and b"LIVE_RECORDING_NOT_CONNECTED" in live.stderr
        assert (recordings / "active.json").read_bytes() == pointer
        assert sha256(path.read_bytes()).hexdigest() == before_cli
        assert all(sha256(p.read_bytes()).hexdigest() == value for p, value in hashes.items())
        with repo._session() as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        print("scenario: separate CLI record/verify; steps=9; records=11; exact trace; origins=authored; unknown rejected")
        print("live record: unavailable; exit=1; active set preserved; working files unchanged")
        report = dict(synthetic=True, models_called=False, filters=True, passport_http=True, restart=True,
            decision_history_preserved=True, private_files_unchanged=True, cli_database_unchanged=True,
            scenario_trace_hash=verified["trace_hash"], scenario_workspace=verified["workspace"],
            scenario_steps=9, scenario_records=11, live_record_connected=False, integrity="ok")
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: actual HTTP/CLI/restart and offline scenario; no working database or model calls")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        if process is not None:
            stop(process)
        client.close()


if __name__ == "__main__":
    main()
