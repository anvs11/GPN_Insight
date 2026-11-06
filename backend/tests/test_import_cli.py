import json
import os
import subprocess
import sys

from app.config import REPO_ROOT
from app.db import initialize
from app.repositories import Repository


def call(path, manifest, *, preview=False):
    return subprocess.run([sys.executable, "-m", "app.cli", "import", "--manifest", str(manifest),
        *(["--dry-run"] if preview else [])], cwd=REPO_ROOT / "backend",
        env={**os.environ, "APP_DB_PATH": str(path), "PYTHONUTF8": "1"}, capture_output=True, text=True, encoding="utf-8", timeout=10)


def test_cli_preview_commit_repeat_reopen_without_init(tmp_path):
    path = tmp_path / "cli.db"
    initialize(path)
    manifest = REPO_ROOT / "data/demo/manifest.json"
    before = path.read_bytes()
    preview = call(path, manifest, preview=True)
    assert preview.returncode == 0 and json.loads(preview.stdout)["counts"]["inserted"] == 4
    assert path.read_bytes() == before and Repository(path).list_imports() == []
    first = call(path, manifest)
    assert first.returncode == 0 and json.loads(first.stdout)["counts"]["inserted"] == 4
    repeat = call(path, manifest)
    assert repeat.returncode == 0 and json.loads(repeat.stdout)["counts"]["unchanged"] == 4
    assert len(Repository(path).list_imports()) == 2


def test_cli_missing_db_is_an_error_not_implicit_initialization(tmp_path):
    path = tmp_path / "missing.db"
    manifest = REPO_ROOT / "data/demo/manifest.json"
    preview = call(path, manifest, preview=True)
    assert preview.returncode == 1 and json.loads(preview.stdout)["can_commit"] is False
    committed = call(path, manifest)
    assert committed.returncode == 1 and "PREVIEW_DATABASE_MISSING" in committed.stderr
    assert not path.exists()


def test_cli_invalid_or_oversized_file_never_echoes_source(tmp_path):
    from app.ingestion.importer import MAX_MANIFEST_BYTES
    path = tmp_path / "cli.db"
    initialize(path)
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"SENSITIVE_SENTINEL":')
    before = path.read_bytes()
    preview = call(path, manifest, preview=True)
    assert preview.returncode == 1 and json.loads(preview.stdout)["status"] == "invalid"
    bad = call(path, manifest)
    assert bad.returncode == 1 and "SENSITIVE_SENTINEL" not in bad.stderr + bad.stdout
    manifest.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1))
    large = call(path, manifest)
    assert large.returncode == 1 and "MANIFEST_TOO_LARGE" in large.stderr
    assert path.read_bytes() == before


def test_cli_common_service_never_calls_initialize(tmp_path, monkeypatch, capsys):
    import app.cli as cli
    from app.config import Settings
    path = tmp_path / "cli.db"
    initialize(path)
    monkeypatch.setattr(sys, "argv", ["cli", "import", "--manifest", str(REPO_ROOT / "data/demo/manifest.json")])
    monkeypatch.setattr(cli, "Settings", lambda: Settings(_env_file=None, app_db_path=path))
    monkeypatch.setattr(cli, "initialize", lambda *args: (_ for _ in ()).throw(AssertionError("Import called init-db")))
    assert cli.main() == 0 and json.loads(capsys.readouterr().out)["counts"]["inserted"] == 4


def test_dry_run_cannot_be_silently_ignored_by_other_commands(tmp_path):
    path = tmp_path / "must-not-be-created.db"
    result = subprocess.run([sys.executable, "-m", "app.cli", "init-db", "--dry-run"], cwd=REPO_ROOT / "backend",
        env={**os.environ, "APP_DB_PATH": str(path), "PYTHONUTF8": "1"}, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 2 and "только для import" in result.stderr and not path.exists()
