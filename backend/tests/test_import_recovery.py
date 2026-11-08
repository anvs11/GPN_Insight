from contextlib import contextmanager
from io import BytesIO
import json
import os
import subprocess
import sys

import pytest

from app.config import REPO_ROOT
from app.db import initialize
from app.ingestion.media_files import open_import_workspace, reconcile_import_storage
from app.repositories import RepositoryError
from scripts.media_import_fixture import manifest_for, wav_bytes
from test_upload_security import import_files, media_context


HARNESS = r'''
from contextlib import contextmanager
from io import BytesIO
import os,sys
from pathlib import Path
from app.config import Settings
from app.ingestion.importer import import_tickets
from app.ingestion.media_files import open_import_workspace
from app.repositories import Repository
from scripts.media_import_fixture import manifest_for,wav_bytes
mode,base=sys.argv[1],Path(sys.argv[2])
settings=Settings(_env_file=None,app_db_path=base/'media.db',app_tmp_root=base/'temporary',app_upload_root=base/'uploads')
repo=Repository(settings.app_db_path)
if mode=='transaction':
    original=Repository._session
    @contextmanager
    def pause(self,write=False):
        with original(self,write=write) as db:
            yield db
            if write: os._exit(70)
    Repository._session=pause
if mode in {'moved','committed'}:
    original_register=repo.register_dataset
    def register(*args,**kwargs):
        if mode=='moved': os._exit(70)
        original_register(*args,**kwargs)
        os._exit(70)
    repo.register_dataset=register
if mode=='link-window':
    original_link=os.link
    def link(*args,**kwargs):
        original_link(*args,**kwargs)
        os._exit(70)
    os.link=link
files={'call.wav':wav_bytes()}
with open_import_workspace(settings,repo,dry_run=False) as workspace:
    workspace.stage_stream('call.wav',BytesIO(files['call.wav']))
    if mode=='staging': os._exit(70)
    import_tickets(manifest_for(files),workspace.uploads,repository=repo,workspace=workspace)
raise RuntimeError('Fault injection was not reached')
'''


@pytest.mark.parametrize("mode", ["staging", "link-window", "moved", "transaction", "committed"])
def test_real_process_stop_and_repeat(media_context, mode):
    settings, repo = media_context
    process = subprocess.run([sys.executable, "-c", HARNESS, mode, str(repo.path.parent)],
        cwd=REPO_ROOT / "backend", capture_output=True, text=True, timeout=15)
    assert process.returncode == 70, process.stderr
    # Как в lifespan: SQLite восстанавливает hot journal до файловой сверки.
    initialize(repo.path)
    before = repo.registered_materials()
    assert len(before) == (1 if mode == "committed" else 0)
    report = reconcile_import_storage(repo, settings)
    assert report.issues == [], report
    assert not list(settings.app_tmp_root.glob("imports/*"))
    assert all((settings.app_upload_root / material.storage_path).exists() for material in before)
    result = import_files(media_context, {"call.wav": wav_bytes()})
    assert (result.counts.unchanged, result.counts.inserted) == ((1, 0) if before else (0, 1))
    assert len(repo.registered_materials()) == 1


def test_unknown_registry_commit_retains_journal_and_bytes(media_context, monkeypatch):
    from app.ingestion import media_files
    settings, repo = media_context
    original = media_files._cleanup_final
    monkeypatch.setattr(media_files, "_cleanup_final", lambda *a: (_ for _ in ()).throw(RepositoryError("REGISTRY_UNAVAILABLE", "Нет данных", 503)))
    with pytest.raises(RepositoryError) as error:
        import_files(media_context, {"call.wav": wav_bytes()})
    assert error.value.code == "IMPORT_CLEANUP_PENDING"
    assert len(repo.list_imports()) == 1
    assert list(settings.app_tmp_root.glob("imports/*/journal.json"))
    material = repo.registered_materials()[0]
    assert (settings.app_upload_root / material.storage_path).exists()
    monkeypatch.setattr(media_files, "_cleanup_final", original)
    assert not reconcile_import_storage(repo, settings).issues
    assert import_files(media_context, {"call.wav": wav_bytes()}).counts.unchanged == 1


def test_readonly_recovery_and_missing_registry_never_remove_staging(media_context, monkeypatch):
    settings, repo = media_context
    with open_import_workspace(settings, repo, dry_run=False) as workspace:
        upload = workspace.stage_stream("call.wav", BytesIO(wav_bytes()))
        workspace.retain = True
    assert reconcile_import_storage(repo, settings, read_only=True).issues == []
    assert upload.temporary_path.exists()
    monkeypatch.setattr(repo, "registered_materials", lambda: (_ for _ in ()).throw(RepositoryError("REGISTRY_DOWN", "Не удалось прочитать реестр", 503)))
    assert reconcile_import_storage(repo, settings).issues == ["REGISTRY_DOWN"]
    assert upload.temporary_path.exists()


@pytest.mark.parametrize("kind", ["malformed", "foreign-owner", "escape", "symlink", "unknown-file"])
def test_ambiguous_journals_are_preserved(media_context, kind):
    settings, repo = media_context
    with open_import_workspace(settings, repo, dry_run=False) as workspace:
        upload = workspace.stage_stream("call.wav", BytesIO(wav_bytes()))
        workspace.retain = True
    journal = workspace.folder / "journal.json"
    value = json.loads(journal.read_text())
    if kind == "malformed": journal.write_text("{")
    elif kind == "foreign-owner":
        value["owner"] = "0" * 64
        journal.write_text(json.dumps(value))
    elif kind == "escape":
        value["files"] = [{"storage_path": "../outside.wav", "byte_size": 1, "content_hash": "0" * 64}]
        journal.write_text(json.dumps(value))
    elif kind == "symlink":
        journal.unlink()
        journal.symlink_to(repo.path)
    else: (workspace.folder / "unknown.txt").write_text("Preserve")
    db_before = repo.path.read_bytes()
    assert reconcile_import_storage(repo, settings).issues
    assert upload.temporary_path.exists() and (journal.exists() or journal.is_symlink())
    assert repo.path.read_bytes() == db_before


def test_owner_conflict_and_history_survive_wrong_database(media_context, tmp_path):
    from app.repositories import Repository
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    wrong = Repository(tmp_path / "wrong.db")
    initialize(wrong.path)
    material = repo.registered_materials()[0]
    assert reconcile_import_storage(wrong, settings).issues == ["IMPORT_STORAGE_OWNER_CONFLICT"]
    assert (settings.app_upload_root / material.storage_path).exists()


def test_preexisting_final_directory_is_never_treated_as_ours(media_context):
    from app.ingestion.importer import import_tickets
    settings, repo = media_context
    import_files(media_context, {"call.wav": wav_bytes()})
    with open_import_workspace(settings, repo, dry_run=False) as workspace:
        upload = workspace.stage_stream("other.wav", BytesIO(wav_bytes()))
        collision = settings.app_upload_root / workspace.storage_path(upload, "audio/wav")
        collision.parent.mkdir()
        collision.write_bytes(wav_bytes())
        with pytest.raises(RepositoryError):
            import_tickets(manifest_for({"other.wav": b""}), workspace.uploads,
                           repository=repo, workspace=workspace)
        assert collision.read_bytes() == wav_bytes()
    assert collision.read_bytes() == wav_bytes()
    assert "IMPORT_JOURNAL_INVALID" in reconcile_import_storage(repo, settings).issues
    assert collision.read_bytes() == wav_bytes()
    assert len(repo.list_imports()) == 1


def test_deeply_corrupt_journal_is_reported_and_preserved(media_context):
    settings, repo = media_context
    with open_import_workspace(settings, repo, dry_run=False) as workspace:
        upload = workspace.stage_stream("call.wav", BytesIO(wav_bytes()))
        workspace.retain = True
    journal = workspace.folder / "journal.json"
    journal.write_text("[" * 25000 + "0" + "]" * 25000)
    report = reconcile_import_storage(repo, settings)
    assert report.issues == ["IMPORT_JOURNAL_INVALID"]
    assert upload.temporary_path.read_bytes() == wav_bytes() and journal.exists()
