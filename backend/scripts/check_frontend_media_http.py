"""React/jsdom действия против отдельного настоящего backend; не browser screenshot."""

from hashlib import sha256
import json
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from app.config import REPO_ROOT
from scripts.media_import_fixture import synthetic_files
from scripts.partial_pipeline_fixture import prepare_partial_ui_demo


def main():
    os.environ["APP_WEEKLY_ARCHIVE_ENABLED"] = "false"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", action="store_true", help="Разнообразный demo и новое рабочее пространство")
    args = parser.parse_args()
    folder = REPO_ROOT / "var" / ("frontend-media-http-" + uuid4().hex)
    protected = {p: sha256(p.read_bytes()).hexdigest() for p in [REPO_ROOT / ".env", *REPO_ROOT.glob("var/*.db")] if p.is_file()}
    if args.workspace:
        from scripts.prepare_acceptance_demo import prepare_acceptance_demo
        demo, profile = prepare_acceptance_demo(folder)
        saved = next(c for c in demo.repo.list_topics("replay", support_level="all") if c.topic.owner)
    else:
        demo, profile, saved = prepare_partial_ui_demo(folder)
    fixture_files = folder / "selected-files"
    fixture_files.mkdir(exist_ok=True)
    mimes = {a.path: a.mime_type for a in demo.ticket.assets}
    files = []
    for name, content in synthetic_files().items():
        path = fixture_files / name
        path.write_bytes(content)
        files.append(dict(name=name, path=str(path), mime=mimes[name]))
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps(dict(dataset_id="synthetic-partial-media-v1", synthetic=True,
        tickets=[demo.ticket.model_dump(mode="json")]), ensure_ascii=False), encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    fixture = folder / "ui-fixture.json"
    fixture.write_text(json.dumps(dict(baseUrl=f"http://127.0.0.1:{port}", manifest=str(manifest), files=files,
        sourceId=demo.ticket.source_id, topicId=saved.topic.topic_id, sourceCount=5 if args.workspace else 1)), encoding="utf-8")
    env = dict(os.environ, APP_DB_PATH=str(demo.repo.path), APP_UPLOAD_ROOT=str(demo.settings.app_upload_root),
        APP_TMP_ROOT=str(demo.settings.app_tmp_root), APP_MODEL_MODE="replay", PYTHONUTF8="1",
        APP_PROCESSING_PROFILE_PATH=str(demo.settings.app_processing_profile_path), APP_RECORDINGS_ROOT=str(demo.settings.app_recordings_root),
        APP_MEDIA_RECORDINGS_PATH=str(demo.recordings), APP_MEDIA_CACHE_ROOT=str(demo.settings.app_media_cache_root),
        APP_REPLAY_MODEL_ID=profile.model_id, APP_REPLAY_NORMALIZATION_HASH=profile.parameters["normalization_hash"])
    def start():
        return subprocess.Popen([sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
            "--workers", "1", "--no-access-log", "--log-level", "warning"], cwd=REPO_ROOT / "backend", env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    def ready():
        with httpx.Client(trust_env=False, timeout=2) as client:
            for _ in range(150):
                try:
                    result = client.get(f"http://127.0.0.1:{port}/api/v1/health")
                    if result.status_code == 200:
                        assert result.json()["pipeline_ready"]
                        break
                except httpx.TransportError:
                    pass
                if process.poll() is not None:
                    raise RuntimeError("Synthetic backend exited")
                time.sleep(.05)
            else:
                raise RuntimeError("Synthetic backend startup timeout")
    process = start()
    try:
        ready()
        portable = REPO_ROOT / "var/tooling/node/active-path.txt"
        node = str(Path(portable.read_text(encoding="utf-8-sig").strip()) / ("node.exe" if os.name == "nt" else "bin/node")) if portable.is_file() else shutil.which("node")
        if not node or not Path(node).is_file():
            raise RuntimeError("Node is unavailable; install frontend dependencies per README")
        ui_env = dict(os.environ, GPN_UI_HTTP_FIXTURE=str(fixture))
        outcome = subprocess.run([node, str(REPO_ROOT / "frontend/node_modules/vitest/vitest.mjs"), "run", "--config", "vitest.http.config.ts"],
            cwd=REPO_ROOT / "frontend", env=ui_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        (folder / "frontend-check.txt").write_bytes(outcome.stdout)
        if outcome.returncode:
            print(outcome.stdout.decode("utf-8", errors="replace"))
            raise RuntimeError("Frontend HTTP acceptance failed; isolated report retained")
        with httpx.Client(trust_env=False, timeout=5) as client:
            archive = client.get(f"http://127.0.0.1:{port}/api/v1/reports/archive").json()
            assert archive["total"] == 1
            report_url = f"http://127.0.0.1:{port}/api/v1/reports/archive/{archive['items'][0]['report_id']}"
            saved = client.get(report_url).json()
            stop(process)
            process = start()
            ready()
            assert client.get(report_url).json() == saved
        assert all(sha256(p.read_bytes()).hexdigest() == h for p, h in protected.items())
        (folder / "result.json").write_text(json.dumps(dict(synthetic=True, origin="authored", live_model_calls=False,
            frontend_http_actions=5, browser_visual_acceptance=False, private_files_preserved=True, ordinary_backend=True, archive_process_restart_preserved=True), indent=2), encoding="utf-8")
        print("PASS: 5 React/jsdom -> real HTTP -> SQLite -> React actions; media preview/commit/repeat; partial/unknown/media; summary/week; retry/decision; workspace/report archive")
        print("synthetic=true; origin=authored; live_model_calls=false; browser_visual_acceptance=false")
        print("PASS: archived report unchanged after actual backend process restart")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        stop(process)


if __name__ == "__main__":
    main()
