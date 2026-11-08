from io import BytesIO
import json
from hashlib import sha256
import os
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from app.config import REPO_ROOT
from app.ingestion.importer import import_tickets
from app.ingestion.media_files import open_import_workspace, reconcile_import_storage
from app.main import create_app
from app.repositories import RepositoryError
from scripts.media_import_fixture import manifest_for, synthetic_files, wav_bytes, write_fixture
from test_upload_security import media_context, import_files


def test_repeat_delta_history_and_out_of_order_files(media_context):
    settings, repo = media_context
    files = synthetic_files()
    assert import_files(media_context, files, order=list(reversed(files))).counts.inserted == 4
    original = repo.registered_materials()
    for material in original:
        assert sha256((settings.app_upload_root / material.storage_path).read_bytes()).hexdigest() == material.content_hash
        assert material.original_filename not in material.storage_path
    assert import_files(media_context, files).counts.unchanged == 4
    assert repo.registered_materials() == original
    files["call.wav"] = wav_bytes(sample=1)
    assert import_files(media_context, files).counts.model_dump() == {"inserted": 0, "updated": 1, "unchanged": 3, "invalid": 0}
    assert len(repo.get_source_history("SYNTHETIC-MEDIA-1")) == 2
    assert len(repo.registered_materials()) == 5
    assert all((settings.app_upload_root / m.storage_path).exists() for m in original)
    assert len(list((settings.app_upload_root / "requests").rglob("*.wav"))) == 2
    assert reconcile_import_storage(repo, settings).outcome == "ok"


@pytest.mark.parametrize("variant", ["missing", "extra", "wrong-case", "wrong-mime", "duplicate-name", "duplicate-id"])
def test_exact_manifest_file_binding(media_context, variant):
    files = synthetic_files()
    manifest = json.loads(manifest_for(files))
    if variant == "missing": files.pop("call.wav")
    elif variant == "extra": files["extra.wav"] = wav_bytes()
    elif variant == "wrong-case": files["CALL.wav"] = files.pop("call.wav")
    elif variant == "wrong-mime": manifest["tickets"][0]["assets"][0]["mime_type"] = "audio/mpeg"
    elif variant == "duplicate-name": manifest["tickets"][1]["assets"][0]["path"] = "call.wav"
    else: manifest["tickets"][1]["assets"][0]["asset_id"] = "synthetic-asset-1"
    response = import_files(media_context, files, manifest=json.dumps(manifest), preview=True)
    assert not response.can_commit and response.counts.invalid == 1
    assert media_context[1].list_imports() == []


def test_http_preview_repeat_metadata_and_media_run_is_still_explicitly_unavailable(media_context):
    settings, repo = media_context
    files = synthetic_files()
    parts = [("manifest", ("manifest.json", manifest_for(files), "application/json"))]
    parts += [("files", (name, data, "application/octet-stream")) for name, data in reversed(list(files.items()))]
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        before = repo.path.read_bytes()
        result = client.post("/api/v1/imports?dry_run=true", files=parts)
        assert result.status_code == 200 and result.json()["can_commit"]
        assert repo.path.read_bytes() == before and not settings.app_upload_root.exists()
        assert client.post("/api/v1/imports", files=parts).json()["counts"]["inserted"] == 4
        assert client.post("/api/v1/imports", files=parts).json()["counts"]["unchanged"] == 4
        source = client.get("/api/v1/sources/SYNTHETIC-MEDIA-1").json()
        assert source["ticket"]["assets"][0]["kind"] == "audio" and source["document"] is None
        assert client.post("/api/v1/runs", json={}).status_code == 503
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert not health["pipeline_ready"]
        assert not any("STORAGE" in code or "JOURNAL" in code for code in health["integration_errors"])
        assert len(client.get("/api/v1/imports").json()) == 2


def test_cli_uses_same_validation_and_preserves_preview(media_context, tmp_path):
    settings, repo = media_context
    manifest = write_fixture(tmp_path / "dataset")
    env = {**os.environ, "APP_DB_PATH": str(repo.path), "APP_TMP_ROOT": str(settings.app_tmp_root),
           "APP_UPLOAD_ROOT": str(settings.app_upload_root), "APP_MODEL_MODE": "replay", "PYTHONUTF8": "1"}
    def call(*extra):
        return subprocess.run([sys.executable, "-m", "app.cli", "import", "--manifest", str(manifest), *extra],
            cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    before = repo.path.read_bytes()
    preview = call("--dry-run")
    assert preview.returncode == 0 and json.loads(preview.stdout)["can_commit"]
    assert repo.path.read_bytes() == before and not settings.app_upload_root.exists()
    assert json.loads(call().stdout)["counts"]["inserted"] == 4
    assert json.loads(call().stdout)["counts"]["unchanged"] == 4


def test_cli_rejects_actual_symlink(media_context, tmp_path, create_symlink):
    settings, repo = media_context
    manifest = write_fixture(tmp_path / "dataset")
    (manifest.parent / "call.wav").unlink()
    create_symlink(manifest.parent / "call.wav", repo.path)
    env = {**os.environ, "APP_DB_PATH": str(repo.path), "APP_TMP_ROOT": str(settings.app_tmp_root),
           "APP_UPLOAD_ROOT": str(settings.app_upload_root), "PYTHONUTF8": "1"}
    failed = subprocess.run([sys.executable, "-m", "app.cli", "import", "--manifest", str(manifest)],
        cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert failed.returncode == 1 and "FILE_LINK_UNSUPPORTED" in failed.stderr
    assert repo.list_imports() == []


def test_actual_server_upload_larger_than_previous_text_envelope(media_context):
    settings, repo = media_context
    files = {"call.wav": wav_bytes(60)}
    # 960044 байта WAV + большой, но допустимый manifest > прежнего 1 МиБ +64 КиБ.
    manifest = manifest_for(files) + b" " * 300000
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/imports", files=[("files", ("call.wav", files["call.wav"])), ("manifest", ("manifest.json", manifest))])
        assert response.status_code == 200 and response.json()["counts"]["inserted"] == 1


def test_corrupt_registered_file_is_not_silently_replaced(media_context):
    settings, repo = media_context
    files = {"call.wav": wav_bytes()}
    import_files(media_context, files)
    original = repo.registered_materials()[0]
    path = settings.app_upload_root / original.storage_path
    path.write_bytes(b"broken")
    with pytest.raises(RepositoryError) as exc:
        import_files(media_context, files)
    assert exc.value.code == "REGISTERED_MATERIAL_INTEGRITY_FAILED"
    assert path.read_bytes() == b"broken" and len(repo.list_imports()) == 1
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert "REGISTERED_MATERIAL_INTEGRITY_FAILED" in client.get("/api/v1/health").json()["integration_errors"]


def test_media_delta_preserves_topic_decision_and_manual_history(media_context):
    from app.schemas import DecisionUpdate, ManualOverride
    from app.storage import now
    from scripts.media_import_fixture import seed_authored_topic
    settings, repo = media_context
    files = {"call.wav": wav_bytes()}
    import_files(media_context, files)
    topic_id = seed_authored_topic(repo)
    repo.record_decision(topic_id, DecisionUpdate(version=1, status="needs_research", owner="Синтетический владелец", reason="Проверить сохранность"))
    before = repo.get_topic(topic_id, "replay")
    correction = ManualOverride(override_id="synthetic-manual-attach", source_id="SYNTHETIC-MEDIA-1", source_version=1,
        need_id="synthetic-import-need", action="attach", topic_id=topic_id, author="Синтетический аналитик",
        reason="Ручное подтверждение связи по тексту", created_at=now())
    repo.record_override(correction, expected_versions={topic_id: before.topic.version})
    saved = repo.get_topic(topic_id, "replay")
    changes = repo.topic_changes(topic_id)
    assert import_files(media_context, files).counts.unchanged == 1
    assert repo.get_overrides("SYNTHETIC-MEDIA-1") == [correction]
    files["call.wav"] = wav_bytes(sample=2)
    assert import_files(media_context, files).counts.updated == 1
    after = repo.get_topic(topic_id, "replay")
    assert after.topic.topic_id == saved.topic.topic_id and after.topic.status == "needs_research"
    assert after.topic.owner == saved.topic.owner and after.topic.passport == saved.topic.passport
    assert after.history == saved.history and repo.topic_changes(topic_id) == changes
    assert repo.get_overrides("SYNTHETIC-MEDIA-1")[0].requires_recheck
    assert after.stats.ticket_count == 0  # Старое извлечение не приписывается новой версии.
    assert len(repo.registered_materials()) == 2


def test_staged_bytes_are_rechecked_after_preflight_before_sqlite_commit(media_context, monkeypatch):
    settings, repo = media_context
    files = {"call.wav": wav_bytes()}
    with open_import_workspace(settings, repo, dry_run=False) as workspace:
        upload = workspace.stage_stream("call.wav", BytesIO(files["call.wav"]))
        original = repo.preview_dataset_counts
        def changed_after_validation(batch):
            result = original(batch)
            upload.temporary_path.write_bytes(wav_bytes(sample=3))
            return result
        monkeypatch.setattr(repo, "preview_dataset_counts", changed_after_validation)
        with pytest.raises(RepositoryError) as error:
            import_tickets(manifest_for(files), workspace.uploads, repository=repo, workspace=workspace)
        assert error.value.code == "FILE_CHANGED"
    assert repo.list_imports() == [] and repo.registered_materials() == []


@pytest.mark.parametrize("kind", ["owner", "registry"])
def test_corrupt_storage_metadata_returns_safe_503_without_changing_materials(media_context, kind):
    from app.db import connection
    settings, repo = media_context
    files = {"call.wav": wav_bytes()}
    import_files(media_context, files)
    material = repo.registered_materials()[0]
    target = settings.app_upload_root / material.storage_path
    with TestClient(create_app(settings), base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        if kind == "owner":
            (settings.app_upload_root / ".import-owner.json").write_text("{SENSITIVE_SENTINEL")
        else:
            with connection(repo.path) as db:
                db.execute("UPDATE registered_materials SET body_json=?", ('{"SENSITIVE_SENTINEL":true}',))
        before = repo.path.read_bytes()
        response = client.post("/api/v1/imports", files=[("manifest", ("manifest.json", manifest_for(files))),
                                                        ("files", ("call.wav", files["call.wav"]))])
        assert response.status_code == 503
        assert response.json()["code"].startswith("IMPORT_STORAGE_")
        assert "SENSITIVE_SENTINEL" not in response.text
        assert target.read_bytes() == files["call.wav"]
        assert repo.path.read_bytes() == before and len(repo.list_imports()) == 1


def test_reused_material_checks_only_current_product_but_cleanup_keeps_full_registry(media_context):
    from app.schemas import DatasetRegistration
    settings, repo = media_context
    files = {"call.wav": wav_bytes()}
    import_files(media_context, files)
    source = repo.get_source("SYNTHETIC-MEDIA-1")
    original = repo.registered_materials()[0]
    other = source.ticket.model_copy(update={"product_id": "synthetic-other-product"})
    foreign_material = original.model_copy(update={"storage_path": "missing-other-product.wav"})
    repo.register_dataset(DatasetRegistration(dataset_id="other-product-history", product_id=other.product_id,
                                             synthetic=True, tickets=[other], materials=[foreign_material]))
    assert import_files(media_context, files).counts.unchanged == 1
    assert len(repo.registered_materials()) == 2
    assert (settings.app_upload_root / original.storage_path).read_bytes() == files["call.wav"]
