"""Реальный HTTP/CLI/restart ручных действий на отдельной synthetic SQLite."""

from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from app.config import REPO_ROOT
from app.db import connection, initialize
from app.demo import load_fixture
from app.repositories import Repository


def main():
    folder = REPO_ROOT / "var" / ("human-workflow-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    path = folder / "check.db"
    fixture = load_fixture(REPO_ROOT / "data/contracts/demo_state.json")
    initialize(path)
    Repository(path).seed_demo(fixture)  # Только эта отдельная явно synthetic/authored БД.
    env = dict(os.environ, APP_DB_PATH=str(path), APP_MODEL_MODE="replay", APP_REPLAY_MODEL_ID="",
        APP_REPLAY_NORMALIZATION_HASH="", APP_PROCESSING_PROFILE_PATH=str(folder / "not-configured.json"),
        APP_RECORDINGS_ROOT=str(folder / "recordings"), APP_ISSUE_HOSTS="", PYTHONUTF8="1")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=10, trust_env=False)
    process = None

    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    def start():
        child = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"], cwd=REPO_ROOT / "backend",
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for _ in range(100):
                try:
                    if client.get("/health").status_code == 200:
                        return child
                except httpx.TransportError:
                    pass
                if child.poll() is not None:
                    raise RuntimeError("Synthetic server exited")
                time.sleep(.05)
            raise RuntimeError("Synthetic server startup timeout")
        except BaseException:
            stop(child)
            raise

    def get_card(topic_id):
        response = client.get("/topics/" + topic_id)
        assert response.status_code == 200
        return response.json()

    try:
        process = start()
        assert client.get("/health").json()["pipeline_ready"] is False
        assert client.post("/runs", json={}).status_code == 503
        print("synthetic=true; transport=localhost-http; model_calls=none; pipeline_ready=false")
        source_id = fixture.topic.topic_id
        due = (datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)).isoformat()
        decision = {"version": 1, "status": "deferred", "owner": "Синтетический владелец",
                    "reason": "Ручная синтетическая проверка", "review_after": due}
        saved = client.patch(f"/topics/{source_id}/decision", json=decision)
        assert saved.status_code == 200 and saved.json()["stats"]["review_due"]
        assert client.patch(f"/topics/{source_id}/decision", json=decision).status_code == 409
        print("decision: deferred=200; review_due=true; stale-version=409")

        command = {"version": saved.json()["topic"]["version"], "need_ids": [fixture.needs[0].need_id],
                   "author": "Синтетический аналитик", "reason": "Проверить отдельную потребность"}
        result = client.post(f"/topics/{source_id}/exclude", json=command)
        assert result.status_code == 200
        excluded = result.json()
        assert get_card(source_id)["stats"]["ticket_count"] == 3
        assert client.post(f"/topics/{source_id}/exclude", json=command).json() == excluded
        review_id = excluded["review_ids"][0]
        assert len(client.get("/review-items").json()) == 1
        need = fixture.needs[0]
        ref = {"source_id": need.source_id, "fragment_id": need.fragment_id, "quote": need.evidence_quote}
        resolution = {"version": 1, "action": "create", "author": "Синтетический аналитик", "reason": "Создать вручную",
            "proposed_topic": {"title": "Ручная синтетическая тема", "summary": need.statement, "scenario": need.scenario,
                "need_statement": need.statement, "scope": "Ручные границы для проверки",
                "inclusion_examples": [{"text": need.statement, "evidence_refs": [ref]}]}}
        response = client.post(f"/review-items/{review_id}/resolve", json=resolution)
        assert response.status_code == 200
        review = response.json()
        target_id = review["resolution"]["topic_id"]
        assert client.post(f"/reviews/{review_id}/resolve", json=resolution).json() == review
        assert len(client.get(f"/reviews/{review_id}/history").json()) == 2
        print("exclude/review: count=3; one queue item; create=200; exact repeats do not duplicate history")

        target = get_card(target_id)
        linked = client.patch(f"/topics/{target_id}/decision", json={"version": target["topic"]["version"], "status": "linked",
            "owner": "Владелец целевой темы", "reason": "Сохранить при объединении", "existing_issue_ref": "SYNTHETIC-ISSUE-1"})
        assert linked.status_code == 200
        keep = linked.json()
        preview = client.post(f"/topics/{source_id}/merge-preview", json={"target_topic_id": target_id})
        assert preview.status_code == 200 and preview.json()["decision_conflict"]
        merge = {"version": preview.json()["source"]["topic"]["version"], "target_topic_id": target_id,
            "target_version": preview.json()["target"]["topic"]["version"], "author": "Синтетический аналитик", "reason": "Объединить явно"}
        assert client.post(f"/topics/{source_id}/merge", json=merge).status_code == 409
        merge["confirm_decision_conflict"] = True
        merged = client.post(f"/topics/{source_id}/merge", json=merge)
        assert merged.status_code == 200
        receipt = merged.json()
        assert client.post(f"/topics/{source_id}/merge", json=merge).json() == receipt
        restored = get_card(target_id)
        assert restored["stats"]["ticket_count"] == 4 and restored["history"] == keep["history"]
        assert restored["topic"]["status"] == "linked" and restored["topic"]["owner"] == keep["topic"]["owner"]
        assert get_card(source_id)["topic"]["canonical_topic_id"] == target_id
        print("merge: preview conflict=true; unconfirmed=409; confirmed=200; target decision/history/count=4 preserved")

        report = client.get("/report")
        assert report.status_code == 200 and "Уникальных источников в периоде: 4." in report.text
        assert "authored" in report.text and "replay" in report.text
        assert all(ticket.text not in report.text for ticket in fixture.sources)
        (folder / "http-report.md").write_text(report.text, encoding="utf-8")
        snapshot_history = client.get(f"/topics/{target_id}/changes").json()
        stop(process)
        process = None
        process = start()
        assert get_card(target_id) == restored
        assert client.get(f"/topics/{target_id}/changes").json() == snapshot_history
        assert client.get(f"/reviews/{review_id}").json() == review
        assert client.post(f"/topics/{source_id}/merge", json=merge).json() == receipt
        print("restart: canonical link, review, overrides, decisions and history=preserved")

        stop(process)
        process = None
        before = sha256(path.read_bytes()).hexdigest()
        cli = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", "report", "--output", str(folder / "cli-report.md")],
            env=env, cwd=REPO_ROOT / "backend", capture_output=True, text=True, encoding="utf-8")
        assert cli.returncode == 0 and "Уникальных источников в периоде: 4." in (folder / "cli-report.md").read_text(encoding="utf-8")
        assert sha256(path.read_bytes()).hexdigest() == before
        with connection(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        summary = {"synthetic": True, "model_calls": False, "ordinary_main": True, "restart": True,
            "counts": {"before": 4, "excluded": 3, "merged": 4}, "exact_repeats": True,
            "version_conflict": 409, "merge_decision_confirmation": True, "decision_history_preserved": True,
            "cli_report_utf8": True, "report_database_unchanged": True}
        (folder / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: actual manual workflow HTTP/CLI/restart; SQLite integrity/FK; report without model or working database")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        if process is not None:
            stop(process)
        client.close()


if __name__ == "__main__":
    main()
