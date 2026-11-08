"""Настоящий HTTP/CLI/restart на отдельной synthetic БД; распознавание не вызывается."""

from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from app.config import REPO_ROOT
from app.repositories import Repository
from scripts.media_import_fixture import seed_authored_topic, wav_bytes, write_fixture


def main() -> None:
    folder = REPO_ROOT / "var" / ("media-import-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    manifest = write_fixture(folder / "dataset")
    path, uploads, temporary = folder / "check.db", folder / "uploads", folder / "temporary"
    private = [REPO_ROOT / ".env", *list((REPO_ROOT / "var").glob("*.db"))]
    hashes = {p: sha256(p.read_bytes()).hexdigest() for p in private if p.is_file()}
    env = dict(os.environ, APP_DB_PATH=str(path), APP_UPLOAD_ROOT=str(uploads), APP_TMP_ROOT=str(temporary),
        APP_MODEL_MODE="replay", APP_PROCESSING_PROFILE_PATH=str(folder / "unconfigured-profile.json"),
        APP_RECORDINGS_ROOT=str(folder / "no-recordings"), APP_REPLAY_MODEL_ID="", APP_REPLAY_NORMALIZATION_HASH="",
        TEXT_MODEL_BASE_URL="", ASR_BASE_URL="", VL_BASE_URL="", PYTHONUTF8="1")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=15, trust_env=False)
    process = None
    log = (folder / "server.log").open("xb")

    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    def start():
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"],
            cwd=REPO_ROOT / "backend", env=env, stdout=log, stderr=log)
        for _ in range(100):
            try:
                if client.get("/health").status_code == 200:
                    return child
            except httpx.TransportError:
                pass
            if child.poll() is not None:
                raise RuntimeError("Isolated ordinary server exited; see its local log")
            time.sleep(.05)
        stop(child)
        raise RuntimeError("Isolated server startup timeout")

    def parts():
        files = [("manifest", ("manifest.json", manifest.read_bytes(), "application/json"))]
        data = json.loads(manifest.read_bytes())
        for ticket in reversed(data["tickets"]):
            asset = ticket["assets"][0]
            files.append(("files", (asset["path"], (manifest.parent / asset["path"]).read_bytes(), "application/octet-stream")))
        return files

    try:
        process = start()
        repo = Repository(path)
        before = path.read_bytes()
        preview = client.post("/imports?dry_run=true", files=parts())
        assert preview.status_code == 200 and preview.json()["counts"]["inserted"] == 4
        assert path.read_bytes() == before and not uploads.exists() and not temporary.exists()
        committed = client.post("/imports", files=parts())
        assert committed.status_code == 200 and committed.json()["counts"]["inserted"] == 4
        initial = repo.registered_materials()
        assert len(initial) == 4
        print("synthetic=true; server=ordinary app.main:app; formats=WAV/MP3/PNG/JPEG; models_called=false")
        print("preview: inserted=4; SQLite/import_log/uploads unchanged; temporary files removed")
        assert client.post("/imports", files=parts()).json()["counts"]["unchanged"] == 4
        assert repo.registered_materials() == initial
        topic_id = seed_authored_topic(repo)
        saved = client.patch("/topics/" + topic_id + "/decision", json={"version": 1, "status": "needs_research",
            "owner": "Синтетический владелец", "reason": "Проверка сохранности при изменении вложения"})
        assert saved.status_code == 200
        history = saved.json()["history"]
        (manifest.parent / "call.wav").write_bytes(wav_bytes(sample=1))
        delta = client.post("/imports", files=parts())
        assert delta.json()["counts"] == {"inserted": 0, "updated": 1, "unchanged": 3, "invalid": 0}
        card = client.get("/topics/" + topic_id).json()
        assert card["topic"]["owner"] == "Синтетический владелец" and card["history"] == history
        assert card["stats"]["ticket_count"] == 0  # Прежняя потребность не переносится на новую версию.
        assert len(repo.registered_materials()) == 5
        assert all((uploads / m.storage_path).is_file() for m in initial)
        missing = client.post("/imports", files=parts()[:-1])
        assert missing.status_code == 422 and missing.json()["code"] == "MEDIA_FILES_MISMATCH"
        assert client.post("/runs", json={}).status_code == 503
        print("commit/repeat/delta: inserted4 -> unchanged4 -> updated1/unchanged3; old registered paths retained")
        print("decision: topic ID/owner/history retained; obsolete need not counted for changed source")
        stop(process)
        process = start()
        assert client.post("/imports", files=parts()).json()["counts"]["unchanged"] == 4
        assert client.get("/topics/" + topic_id).json()["history"] == history
        assert not any("STORAGE" in code or "JOURNAL" in code for code in client.get("/health").json()["integration_errors"])
        stop(process)
        process = None
        for arguments in [("--dry-run",), ()]:
            response = subprocess.run([sys.executable, "-m", "app.cli", "import", "--manifest", str(manifest), *arguments],
                cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True, timeout=15)
            assert response.returncode == 0, response.stderr
            assert json.loads(response.stdout)["counts"]["unchanged"] == 4
        assert all(p.is_file() and sha256(p.read_bytes()).hexdigest() == value for p, value in hashes.items())
        for material in repo.registered_materials():
            assert sha256((uploads / material.storage_path).read_bytes()).hexdigest() == material.content_hash
        report = {"synthetic": True, "transport": "localhost-http", "models_called": False,
            "formats": ["WAV", "MP3", "PNG", "JPEG"], "preview_no_persistent_effect": True,
            "commit_repeat_delta_restart_cli": True, "decision_history_preserved": True,
            "registered_materials": len(repo.registered_materials()), "working_private_files_preserved": True,
            "recognition_integrated": False, "crash_recovery_tests": "backend/tests/test_import_recovery.py; separate process-stop verification"}
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("PASS: actual HTTP/CLI/restart; no models; media pipeline remains explicitly unavailable")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        if process is not None:
            stop(process)
        client.close()
        log.close()


if __name__ == "__main__":
    main()
