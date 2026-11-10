import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app import db as database
from app.db import connection, initialize
from app.errors import RepositoryError
from app.repositories import Repository
from app.schemas import DatasetRegistration, Run, RunRequest
from app.storage import now
from app.runs import RunService
from pipeline_adapters import TestComponents


def test_v2_migration_preserves_decisions_and_creates_backup(tmp_path, fixture):
    path = tmp_path / "v2.db"
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    before = repo.get_topic(fixture.topic.topic_id, "replay")
    with connection(path) as db:
        for table in ("report_snapshots", "need_assessments", "processing_needs", "processing_heads", "processing_results"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP TABLE stage_checkpoints")
        db.execute("DROP TABLE run_inputs")
        db.execute("UPDATE schema_version SET version=2")
    result = initialize(path)
    assert result.version == 5 and result.migrated_from == 2 and result.backup_path.exists()
    with connection(result.backup_path) as backup:
        assert backup.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    assert Repository(path).get_topic(fixture.topic.topic_id, "replay") == before
    assert initialize(path).backup_path is None


def test_v2_migration_rolls_back_schema_and_data(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    initialize(path)
    with connection(path) as db:
        db.execute("DROP TABLE stage_checkpoints")
        db.execute("DROP TABLE run_inputs")
        db.execute("UPDATE schema_version SET version=2")
    original = database.migrate_v2
    def fail(db):
        original(db)
        raise RuntimeError("injected v3 failure")
    monkeypatch.setattr(database, "migrate_v2", fail)
    with pytest.raises(RuntimeError): initialize(path)
    with connection(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='run_inputs'").fetchone() is None


def test_start_during_import_commit_fails_without_creating_run(tmp_path):
    path = tmp_path / "guard.db"
    initialize(path)
    repo = Repository(path)
    service = RunService(Repository(path), "replay", TestComponents().adapters())
    try:
        with repo.import_commit():
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(service.start, RunRequest())
                with pytest.raises(RepositoryError) as exc: future.result(timeout=2)
            assert exc.value.code == "IMPORT_ACTIVE" and repo.active_run() is None
            # Ошибка не оставляет блокировку навсегда.
        run = service.start(RunRequest())
        service.futures[run.run_id].result(timeout=2)
        assert repo.get_run(run.run_id).state == "completed"
    finally:
        service.close()


def test_failed_registration_releases_gate(tmp_path, fixture, monkeypatch):
    path = tmp_path / "guard.db"
    initialize(path)
    repo = Repository(path)
    with connection(path) as db:
        db.execute("CREATE TRIGGER reject_log BEFORE INSERT ON import_log BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.Error):
        repo.register_dataset(DatasetRegistration(dataset_id="guard", synthetic=True, tickets=fixture.sources[:1]))
    service = RunService(repo, "replay", TestComponents().adapters())
    try:
        run = service.start(RunRequest())
        service.futures[run.run_id].result(timeout=2)
        assert repo.get_run(run.run_id).state == "completed"
    finally: service.close()


def test_run_start_and_input_snapshot_are_atomic(tmp_path, monkeypatch):
    path = tmp_path / "atomic.db"
    initialize(path)
    repo = Repository(path)
    with connection(path) as db:
        db.execute("CREATE TRIGGER reject_inputs BEFORE INSERT ON run_inputs BEGIN SELECT RAISE(ABORT,'injected'); END")
    service = RunService(repo, "replay", TestComponents().adapters())
    try:
        with pytest.raises(sqlite3.Error): service.start(RunRequest())
        assert repo.active_run() is None
        with connection(path) as db:
            assert db.execute("SELECT count(*) FROM run_records").fetchone()[0] == 0
    finally: service.close()


def test_import_cannot_slip_between_run_check_and_creation(tmp_path, fixture, monkeypatch):
    path = tmp_path / "atomic.db"
    initialize(path)
    repo = Repository(path)
    entered, release = Event(), Event()
    original = Repository.select_run_sources
    def paused(self, request):
        entered.set()
        assert release.wait(3)
        return original(self, request)
    monkeypatch.setattr(Repository, "select_run_sources", paused)
    service = RunService(repo, "replay", TestComponents().adapters())
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.start, RunRequest())
            try:
                assert entered.wait(2)
                with pytest.raises(RepositoryError) as exc:
                    Repository(path).register_dataset(DatasetRegistration(dataset_id="race", synthetic=True, tickets=fixture.sources[:1]))
                assert exc.value.code == "RUN_ACTIVE"
            finally: release.set()
            run = future.result(timeout=3)
        service.futures[run.run_id].result(timeout=2)
        assert repo.get_run(run.run_id).total_materials == 0
        assert repo.list_imports() == []
    finally: service.close()


def test_worker_failure_is_run_level_without_invented_source(tmp_path, monkeypatch):
    from app.pipeline import Pipeline
    path = tmp_path / "failure.db"
    initialize(path)
    repo = Repository(path)
    def fail(self, run_id):
        raise RuntimeError("SENSITIVE-DO-NOT-ECHO")
    monkeypatch.setattr(Pipeline, "execute", fail)
    service = RunService(repo, "replay", TestComponents().adapters())
    try:
        run = service.start(RunRequest())
        service.futures[run.run_id].result(timeout=2)
        failed = repo.get_run(run.run_id)
        assert failed.state == "failed" and failed.failure_code == "PIPELINE_FAILED"
        assert failed.failure_message and failed.errors == []
        assert "SENSITIVE-DO-NOT-ECHO" not in failed.model_dump_json()
    finally: service.close()
