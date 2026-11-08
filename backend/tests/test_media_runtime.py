from hashlib import sha256
from pathlib import Path
import json
import os
import shutil
import stat
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db import connection
from app.media_runtime import resolve_registered_asset
from app.repositories import Repository, RepositoryError
from app.security import checked_path, is_link
from app.schemas import DecisionUpdate
from scripts.media_import_fixture import seed_authored_topic, synthetic_files, wav_bytes
from test_upload_security import media_context, import_files


def resolve(context, *, source_id="SYNTHETIC-MEDIA-1", source_version=1, asset_id="synthetic-asset-1", **extra):
    settings, repo = context
    return resolve_registered_asset(repo, settings, source_id=source_id, source_version=source_version, asset_id=asset_id, **extra)


def snapshot(context):
    settings, repo = context
    return {str(p): sha256(p.read_bytes()).hexdigest() for p in repo.path.parent.rglob("*") if p.is_file()}


def test_four_formats_resolve_readonly_and_survive_repeat_reopen(media_context):
    settings, repo = media_context
    files = synthetic_files()
    assert import_files(media_context, files).counts.inserted == 4
    before = snapshot(media_context)
    resolved = []
    for index, raw in enumerate(files.values(), 1):
        asset = resolve(media_context, source_id=f"SYNTHETIC-MEDIA-{index}", asset_id=f"synthetic-asset-{index}")
        assert asset.absolute_path.is_absolute() and asset.absolute_path.is_relative_to(settings.app_upload_root)
        assert asset.absolute_path.read_bytes() == raw
        assert asset.content_hash == sha256(raw).hexdigest()
        resolved.append(asset)
    assert snapshot(media_context) == before
    assert import_files(media_context, files).counts.unchanged == 4
    before = snapshot(media_context)
    for index, asset in enumerate(resolved, 1):
        assert resolve((settings, Repository(repo.path)), source_id=f"SYNTHETIC-MEDIA-{index}", asset_id=asset.asset_id) == asset
    assert snapshot(media_context) == before


def test_historical_version_does_not_read_current_file_or_lose_decision(media_context):
    settings, repo = media_context
    files = {"call.wav": wav_bytes()}
    import_files(media_context, files)
    old = resolve(media_context)
    topic_id = seed_authored_topic(repo)
    repo.record_decision(topic_id, DecisionUpdate(version=1, status="needs_research", owner="Синтетический владелец", reason="Сохранить историю"))
    history = repo.get_topic(topic_id, "replay").history
    assert import_files(media_context, {"call.wav": wav_bytes(sample=2)}).counts.updated == 1
    before = snapshot(media_context)
    reopened = (settings, Repository(repo.path))
    assert resolve(reopened) == old
    current = resolve(reopened, source_version=2)
    assert current.content_hash != old.content_hash and current.absolute_path != old.absolute_path
    assert old.absolute_path.read_bytes() == files["call.wav"]
    assert repo.get_topic(topic_id, "replay").history == history
    assert repo.get_topic(topic_id, "replay").stats.ticket_count == 0
    assert snapshot(media_context) == before


@pytest.mark.parametrize("kwargs,code", [
    ({"asset_id": "synthetic-asset-2"}, "SOURCE_ASSET_NOT_FOUND"),
    ({"source_id": "unknown"}, "SOURCE_NOT_FOUND"),
    ({"source_version": 2}, "SOURCE_NOT_FOUND"),
    ({"product_id": "other"}, "SOURCE_NOT_FOUND"),
    ({"source_version": True}, "ASSET_CONTEXT_INVALID"),
    ({"source_version": "1"}, "ASSET_CONTEXT_INVALID"),
    ({"source_version": 0}, "ASSET_CONTEXT_INVALID"),
])
def test_wrong_source_version_or_material_never_falls_back(media_context, kwargs, code):
    import_files(media_context, synthetic_files())
    before = snapshot(media_context)
    with pytest.raises(RepositoryError) as exc:
        resolve(media_context, **kwargs)
    assert exc.value.code == code
    assert snapshot(media_context) == before


@pytest.mark.parametrize("kind", ["same-size", "size", "missing", "hardlink", "owner", "owner-missing", "bad-owner"])
def test_runtime_rechecks_integrity_and_owner_without_cleanup(media_context, kind):
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    material = repo.registered_materials()[0]
    path = settings.app_upload_root / material.storage_path
    if kind == "same-size":
        old = path.stat()
        path.write_bytes(wav_bytes(sample=1))
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    elif kind == "size": path.write_bytes(b"SENSITIVE_SENTINEL")
    elif kind == "missing": path.unlink()
    elif kind == "hardlink": os.link(path, path.with_suffix(".link"))
    else:
        marker = settings.app_upload_root / ".import-owner.json"
        if kind == "owner-missing": marker.unlink()
        else: marker.write_text(json.dumps({"schema": 1, "owner": "0" * 64}) if kind == "owner" else "{SENSITIVE_SENTINEL")
    before = snapshot(media_context)
    with pytest.raises(RepositoryError) as exc:
        resolve(media_context)
    assert exc.value.code in {"REGISTERED_MATERIAL_INTEGRITY_FAILED", "FILE_UNAVAILABLE", "FILE_LINK_UNSUPPORTED",
                             "IMPORT_STORAGE_OWNER_CONFLICT", "IMPORT_STORAGE_OWNER_UNCONFIRMED", "IMPORT_STORAGE_OWNER_INVALID"}
    assert "SENSITIVE_SENTINEL" not in str(exc.value) and str(path) not in str(exc.value)
    assert snapshot(media_context) == before


@pytest.mark.parametrize("kind", ["malformed", "binding", "kind", "source-hash", "path", "escape", "missing-link", "oversized"])
def test_corrupt_registry_is_not_accepted_as_approved(media_context, kind):
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    with connection(repo.path) as db:
        material = json.loads(db.execute("SELECT body_json FROM registered_materials").fetchone()[0])
        if kind == "malformed": material = {"SENSITIVE_SENTINEL": True}
        elif kind == "binding": material["asset_id"] = "foreign"
        elif kind == "kind": material.update(kind="image", mime_type="image/png")
        elif kind == "path": material["storage_path"] = "unexpected.wav"
        elif kind == "escape": material["storage_path"] = "../SENSITIVE_SENTINEL.wav"
        elif kind == "oversized": material["byte_size"] = 10 * 1024 * 1024 + 1
        elif kind == "source-hash": db.execute("UPDATE source_versions SET content_hash=?", ("0" * 64,))
        elif kind == "missing-link": db.execute("DELETE FROM source_materials")
        db.execute("UPDATE registered_materials SET body_json=?", (json.dumps(material),))
    before = snapshot(media_context)
    with pytest.raises(RepositoryError) as exc:
        resolve(media_context)
    assert exc.value.code in {"ASSET_REGISTRY_UNAVAILABLE", "ASSET_REGISTRY_CONFLICT", "ASSET_STORAGE_INVALID"}
    assert "SENSITIVE_SENTINEL" not in str(exc.value)
    assert snapshot(media_context) == before


def test_missing_database_and_roots_are_never_created(tmp_path):
    repo = Repository(tmp_path / "missing.db")
    settings = Settings(_env_file=None, app_upload_root=tmp_path / "missing-uploads")
    with pytest.raises(RepositoryError) as exc:
        resolve((settings, repo))
    assert exc.value.code == "PREVIEW_DATABASE_MISSING"
    assert not list(tmp_path.iterdir())


def test_copy_of_database_cannot_claim_another_storage_owner(media_context):
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    copied = repo.path.with_name("other.db")
    shutil.copyfile(repo.path, copied)
    before = snapshot(media_context)
    with pytest.raises(RepositoryError) as exc:
        resolve((settings, Repository(copied)))
    assert exc.value.code == "IMPORT_STORAGE_OWNER_CONFLICT"
    assert snapshot(media_context) == before


def test_missing_upload_root_is_not_created(media_context):
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    other = settings.model_copy(update={"app_upload_root": repo.path.parent / "not-created"})
    before = snapshot(media_context)
    with pytest.raises(RepositoryError) as exc:
        resolve((other, repo))
    assert exc.value.code == "FILE_UNAVAILABLE"
    assert not other.app_upload_root.exists() and snapshot(media_context) == before


def test_reparse_attribute_is_rejected_by_path_check_without_symlink_privilege(tmp_path, monkeypatch):
    path = tmp_path / "test.wav"
    path.write_bytes(wav_bytes())
    real_lstat = Path.lstat
    info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=0x400)
    assert is_link(info)
    monkeypatch.setattr(Path, "lstat", lambda p, *a, **kw: info if p == path else real_lstat(p, *a, **kw))
    with pytest.raises(RepositoryError) as exc:
        checked_path(path, tmp_path)
    assert exc.value.code == "FILE_LINK_UNSUPPORTED"


def test_actual_runtime_symlink_is_rejected(media_context, create_symlink):
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    path = settings.app_upload_root / repo.registered_materials()[0].storage_path
    path.unlink()
    create_symlink(path, repo.path)
    before = snapshot(media_context)
    with pytest.raises(RepositoryError) as exc:
        resolve(media_context)
    assert exc.value.code == "FILE_LINK_UNSUPPORTED"
    assert snapshot(media_context) == before
