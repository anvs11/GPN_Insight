"""Ручная приёмка обычного HTTP bootstrap/restart на отдельной synthetic БД."""

from hashlib import sha256
import json
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from app.config import REPO_ROOT
from app.repositories import Repository
from scripts.partial_pipeline_fixture import damaged_png, prepare_pipeline_demo


def main():
    folder = REPO_ROOT / "var" / ("partial-weekly-check-" + uuid4().hex)
    protected = {p: sha256(p.read_bytes()).hexdigest() for p in [REPO_ROOT / ".env", *REPO_ROOT.glob("var/*.db")] if p.is_file()}
    demo, profile = prepare_pipeline_demo(folder)
    # Только тестовый барьер после сохранения нормализации. Адаптеры/recordings
    # настоящие; этот модуль никогда не используется обычным app.main.
    marker = folder / "interruption-marker"
    harness = folder / "interrupt_harness.py"
    harness.write_text("from dataclasses import replace\nfrom pathlib import Path\nimport time\nfrom app.main import create_app\n"
        "app=create_app()\nservice=app.state.run_service\noriginal=service.adapters.analysis\n"
        "class Barrier:\n"
        " def extract_needs(self, doc):\n"
        f"  Path({str(marker)!r}).write_text('paused',encoding='utf-8')\n"
        "  time.sleep(20)\n  return original.extract_needs(doc)\n"
        " def assign_topic(self,*args): return original.assign_topic(*args)\n"
        "service.adapters=replace(service.adapters,analysis=Barrier())\n", encoding="utf-8")
    env = dict(os.environ, APP_DB_PATH=str(demo.repo.path), APP_UPLOAD_ROOT=str(demo.settings.app_upload_root),
        APP_TMP_ROOT=str(demo.settings.app_tmp_root), APP_MODEL_MODE="replay", PYTHONUTF8="1",
        APP_PROCESSING_PROFILE_PATH=str(demo.settings.app_processing_profile_path),
        APP_RECORDINGS_ROOT=str(demo.settings.app_recordings_root), APP_MEDIA_RECORDINGS_PATH=str(demo.recordings),
        APP_MEDIA_CACHE_ROOT=str(demo.settings.app_media_cache_root), APP_REPLAY_MODEL_ID=profile.model_id,
        APP_REPLAY_NORMALIZATION_HASH=profile.parameters["normalization_hash"],
        TEXT_MODEL_BASE_URL="", TEXT_MODEL_ID="", ASR_BASE_URL="", ASR_MODEL_ID="", VL_BASE_URL="", VL_MODEL_ID="")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=15, trust_env=False)
    process = None

    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    def start(interrupt=False):
        module = "interrupt_harness:app" if interrupt else "app.main:app"
        child = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", module, "--app-dir", str(folder if interrupt else REPO_ROOT / "backend"), "--host", "127.0.0.1",
            "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"],
            cwd=REPO_ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for _ in range(150):
                try:
                    health = client.get("/health")
                    if health.status_code == 200:
                        assert health.json()["pipeline_ready"], health.json()["integration_errors"]
                        return child
                except httpx.TransportError:
                    pass
                if child.poll() is not None:
                    raise RuntimeError("Isolated ordinary server exited")
                time.sleep(.05)
            raise RuntimeError("Isolated server startup timeout")
        except BaseException:
            stop(child)
            raise

    def run():
        response = client.post("/runs", json={})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(200):
            saved = client.get("/runs/" + run_id).json()
            if saved["state"] not in {"queued", "running"}:
                return saved
            time.sleep(.05)
        raise RuntimeError("Isolated run timed out")

    path = damaged_png(demo)
    raw = path.read_bytes()
    try:
        path.write_bytes(b"SYNTHETIC-CORRUPT")
        process = start(interrupt=True)
        interrupted = client.post("/runs", json={}).json()["run_id"]
        for _ in range(200):
            if marker.is_file(): break
            time.sleep(.02)
        assert marker.is_file()
        assert client.post("/runs", json={}).json()["run_id"] == interrupted
        cache_before = {p.name: p.read_bytes() for p in demo.settings.app_media_cache_root.glob("*.json")}
        assert len(cache_before) == 3
        stop(process)
        process = None
        process = start()
        assert client.get("/runs/" + interrupted).json()["state"] == "interrupted"
        first = run()
        assert first["state"] == "completed_with_errors" and first["material_coverage"]["successful"] == 4
        assert first["reused_steps"] >= 1
        assert {p.name: p.read_bytes() for p in demo.settings.app_media_cache_root.glob("*.json")} == cache_before
        partial = client.get(f"/sources/{demo.ticket.source_id}/processing").json()
        cards = client.get("/topics?support_level=all").json()
        assert len(cards) == 1 and cards[0]["stats"]["ticket_count"] == 1
        card = cards[0]
        body = dict(status="needs_research", owner="Синтетический владелец",
            reason="Сохранить решение при дополнении media context.", version=card["topic"]["version"])
        saved = client.patch(f"/topics/{card['topic']['topic_id']}/decision", json=body)
        assert saved.status_code == 200
        saved = saved.json()
        assert client.patch(f"/topics/{card['topic']['topic_id']}/decision", json=body).status_code == 409
        path.write_bytes(raw)
        second = run()
        assert second["state"] == "completed" and second["material_coverage"]["successful"] == 5, second
        current = client.get(f"/topics/{card['topic']['topic_id']}").json()
        assert current["history"] == saved["history"] and current["topic"]["status"] == "needs_research"
        assert current["topic"]["passport"] == saved["topic"]["passport"] and current["stats"]["ticket_count"] == 1
        assert set(saved["topic"]["need_ids"]) < set(current["topic"]["need_ids"])
        historical = client.get(f"/sources/{demo.ticket.source_id}/processing", params={"revision_id": partial["result"]["revision_id"]}).json()
        assert historical["result"] == partial["result"] and not historical["is_current"]
        for asset in demo.ticket.assets:
            url = f"/sources/{demo.ticket.source_id}/versions/1/assets/{asset.asset_id}"
            response = client.get(url)
            assert response.status_code == 200 and response.headers["content-type"] == asset.mime_type
            if asset.kind == "audio":
                ranged = client.get(url, headers={"Range": "bytes=0-15"})
                assert ranged.status_code == 206 and ranged.content == response.content[:16]
        weekly = client.get("/reports/weekly")
        assert weekly.status_code == 200 and "Europe/Moscow" in weekly.text and "category=operational_request" in weekly.text
        (folder / "weekly.md").write_text(weekly.text, encoding="utf-8")
        period = client.get("/reports/weekly/period").json()
        stop(process)
        process = None
        process = start()
        assert client.get(f"/topics/{card['topic']['topic_id']}").json() == current
        third = run()
        assert third["state"] == "completed" and third["completed_steps"] == 0 and third["reused_steps"] > 0
        assert client.get(f"/topics/{card['topic']['topic_id']}").json() == current
        assert all(sha256(p.read_bytes()).hexdigest() == h for p, h in protected.items())
        result = dict(synthetic=True, origin="authored", live_model_calls=False, production_bootstrap=True,
            partial_success=4, retry_success=5, distinct_sources=1, decision_passport_history_preserved=True,
            old_revision_preserved=True, restart_repeat_reused=True, binary_four_formats=True, audio_ranges=True,
            actual_process_interrupted=True, interrupted_material_success_reused=True,
            weekly_period=period, private_files_preserved=True)
        (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        examples = {"synthetic": True, "origin": "authored", "processing": client.get(f"/sources/{demo.ticket.source_id}/processing").json(),
            "topic_summary": client.get(f"/topics/{card['topic']['topic_id']}/assessment-summary").json(),
            "completed_week": period, "completed_run": third}
        (folder / "http-examples.json").write_text(json.dumps(examples, ensure_ascii=False, indent=2), encoding="utf-8")
        print("synthetic=true; origins=authored; server=app.main:app; live_model_calls=false")
        print("PASS: HTTP partial 4/5 -> retry 5/5; topic/source count=1; decision/passport/history preserved; stale=409")
        print("PASS: old revision/evidence preserved; process restart/repeat reused; four binary formats/audio ranges; completed MSK week")
        print("PASS: actual process interruption after normalization; repeated active click; successful media checkpoints reused")
        print("PASS: working .env/SQLite unchanged; own HTTP process stopped on exit")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        path.write_bytes(raw)
        if process is not None:
            stop(process)
        client.close()


if __name__ == "__main__":
    main()
