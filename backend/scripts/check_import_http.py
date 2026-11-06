"""Настоящие CLI/HTTP на отдельной SQLite и localhost; ни моделей, ни рабочей БД."""

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import REPO_ROOT, Settings
from app.imports_api import MAX_BODY_BYTES
from app.ingestion.integration import build_text_profile
from app.ingestion.normalizer import TextNormalizer
from app.replay import stable_id
from app.repositories import Repository
from app.schemas import EvidenceRef, ExtractedNeed, PassportExample, Run, Topic, TopicPassport
from app.storage import now


def prepare_manual_topic(repo, manifest):
    """Явно authored grounding для проверки истории, не результат анализа модели."""
    profile = build_text_profile(Settings(_env_file=None), model_id="synthetic-no-model-called", recording_set_version="http-check-v1")
    normalizer = TextNormalizer(profile)
    needs = []
    for item in manifest["tickets"]:
        source = repo.get_source(item["source_id"])
        local = normalizer.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=source.source_version)
        doc = local.model_copy(update={"fragments": [f.model_copy(update={"fragment_id": stable_id(
            "fragment", local.product_id, local.source_id, local.version, local.content_hash, i)}) for i, f in enumerate(local.fragments)]})
        need = ExtractedNeed(need_id=stable_id("need", doc.product_id, doc.source_id, doc.version, doc.content_hash, 0),
            source_id=doc.source_id, fragment_id=doc.fragments[0].fragment_id, evidence_quote=doc.fragments[0].text,
            scenario="Синтетическое изменение контакта", need_type="operational_request",
            statement="Изменить контакт в заявке", explicit_request=True)
        repo.save_source_analysis(doc, [need])
        needs.append(need)
    refs = [EvidenceRef(source_id=n.source_id, fragment_id=n.fragment_id, quote=n.evidence_quote) for n in needs]
    repo.create_topic(Topic(topic_id="synthetic-http-import-topic", title="Синтетическая тема контакта",
        summary="Явно подготовленная вручную тема для проверки сохранности решений.",
        passport=TopicPassport(scenario=needs[0].scenario, need_statement=needs[0].statement,
            scope="Только синтетические обращения", scope_confirmed=True, version=1,
            inclusion_examples=[PassportExample(text=refs[0].quote, evidence_refs=[refs[0]])]),
        need_ids=[n.need_id for n in needs], evidence=refs), origin="authored")
    return "synthetic-http-import-topic"


def main():
    folder = REPO_ROOT / "var" / ("import-http-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    path = folder / "check.db"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, APP_DB_PATH=str(path), APP_MODEL_MODE="replay", PYTHONUTF8="1",
        APP_REPLAY_MODEL_ID="", APP_REPLAY_NORMALIZATION_HASH="", APP_RECORDINGS_ROOT=str(folder / "recordings"),
        TEXT_MODEL_BASE_URL="", ASR_BASE_URL="", VL_BASE_URL="")
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=10, trust_env=False)
    process = None

    def start():
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"],
            cwd=REPO_ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for _ in range(100):
            try:
                if client.get("/health").status_code == 200:
                    return child
            except httpx.TransportError:
                pass
            if child.poll() is not None:
                raise RuntimeError("HTTP check server exited")
            time.sleep(.05)
        child.terminate()
        child.wait(timeout=5)
        raise RuntimeError("HTTP check server startup timeout")

    def stop(child):
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    manifest_path = REPO_ROOT / "data/demo/manifest.json"
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)

    def post(data=raw, *, preview=False, extra=()):
        return client.post("/imports" + ("?dry_run=true" if preview else ""),
            files=[("manifest", ("manifest.json", data, "application/json")), *extra])

    try:
        process = start()
        repo = Repository(path)
        assert client.post("/runs", json={}).json()["code"] == "INTEGRATION_NOT_READY"
        before = path.read_bytes()
        preview = post(preview=True)
        assert preview.status_code == 200 and preview.json()["counts"]["inserted"] == 4
        assert path.read_bytes() == before and client.get("/imports").json() == []
        committed = post()
        assert committed.status_code == 200 and committed.json()["counts"]["inserted"] == 4
        topic_id = prepare_manual_topic(repo, manifest)
        card = client.get("/topics/" + topic_id).json()
        saved = client.patch("/topics/" + topic_id + "/decision", json={"status": "needs_research", "owner": "Проверяющий",
            "reason": "Синтетическое решение для проверки импорта", "version": card["topic"]["version"]})
        assert saved.status_code == 200
        saved_card = saved.json()
        repeated = post()
        assert repeated.status_code == 200 and repeated.json()["counts"]["unchanged"] == 4
        assert client.get("/topics/" + topic_id).json() == saved_card
        entries = client.get("/imports").json()
        assert len(entries) == 2 and entries[0]["registration_id"] != entries[1]["registration_id"]
        print("synthetic=true; transport=localhost-http; models_called=false; topic_grounding=authored")
        print("preview: 200 valid inserted=4; database/history=unchanged")
        print("commit/repeat: 200 inserted=4 -> 200 unchanged=4; completed_imports=2")
        assert post(b"SENSITIVE_SYNTHETIC_SENTINEL").status_code == 422
        assert post(extra=[("files", ("synthetic.wav", b"synthetic", "audio/wav"))]).status_code == 422
        assert post(preview=True, extra=[("files", ("synthetic.wav", b"synthetic", "audio/wav"))]).json()["can_commit"] is False
        large = client.post("/imports", headers={"Content-Type": "multipart/form-data; boundary=synthetic"},
            content=(b"x" * min(16384, MAX_BODY_BYTES + 1 - n) for n in range(0, MAX_BODY_BYTES + 1, 16384)))
        assert large.status_code == 413
        assert len(client.get("/imports").json()) == 2
        print("invalid/media: 422 or invalid preview; chunked oversized body: 413; no partial import")
        repo.create_run(Run(run_id="synthetic-active-http", mode="replay", state="queued", stage="queued", created_at=now()))
        blocked = post()
        assert blocked.status_code == 409 and blocked.json()["code"] == "RUN_ACTIVE"
        repo.interrupt_active_runs()
        with ThreadPoolExecutor(max_workers=1) as pool:
            # Public transaction holds SQLite's writer lock only for this test.
            # Server preflight can read; its existing import guard is then held
            # while register_dataset waits for the writer, without a new guard.
            with repo.transaction():
                future = pool.submit(post)
                time.sleep(.15)
                blocked_run = client.post("/runs", json={})
                assert blocked_run.status_code == 409 and blocked_run.json()["code"] == "IMPORT_ACTIVE"
            assert future.result(timeout=10).status_code == 200
        print("coordination: 409 RUN_ACTIVE; 409 IMPORT_ACTIVE; guards=reused")
        cli = subprocess.run([sys.executable, "-m", "app.cli", "import", "--manifest", str(manifest_path), "--dry-run"],
            cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert cli.returncode == 0 and json.loads(cli.stdout)["counts"]["unchanged"] == 4
        changed = json.loads(raw)
        changed["tickets"][0]["text"] += " Синтетическое обновление."
        updated = post(json.dumps(changed, ensure_ascii=False).encode("utf-8"))
        assert updated.status_code == 200 and updated.json()["counts"]["updated"] == 1 and updated.json()["counts"]["unchanged"] == 3
        after = client.get("/topics/" + topic_id).json()
        assert after["topic"]["topic_id"] == saved_card["topic"]["topic_id"]
        assert after["topic"]["status"] == "needs_research" and after["topic"]["owner"] == "Проверяющий"
        assert after["history"] == saved_card["history"] and after["stats"]["ticket_count"] == 3
        assert after["pending_source_ids"] == [manifest["tickets"][0]["source_id"]]
        assert repo.get_source(manifest["tickets"][0]["source_id"], version=1).needs
        assert client.post("/runs", json={}).status_code == 503
        print("delta: 200 updated=1 unchanged=3; source_history/topic_id/decision/history=preserved")
        stop(process)
        process = None
        process = start()
        assert client.get("/topics/" + topic_id).json() == after
        assert len(client.get("/imports").json()) == 4
        assert client.post("/runs", json={}).json()["code"] == "INTEGRATION_NOT_READY"
        report = {"synthetic": True, "models_called": False, "transport": "localhost-http",
            "preview_writes": False, "inserted": 4, "repeat_unchanged": 4, "delta_updated": 1,
            "delta_unchanged": 3, "too_large_status": 413, "guard_errors": ["RUN_ACTIVE", "IMPORT_ACTIVE"],
            "decision_history_preserved_after_restart": True, "analysis_error": "INTEGRATION_NOT_READY"}
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: actual HTTP/CLI/restart; old decision/history preserved; analysis remains honestly unavailable")
        print("report:", (folder / "result.json").relative_to(REPO_ROOT))
    finally:
        if process is not None:
            stop(process)
        client.close()


if __name__ == "__main__":
    main()
