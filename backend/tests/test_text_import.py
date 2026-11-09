import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest

from app.config import REPO_ROOT
from app.db import connection, initialize
from app.ingestion.importer import import_tickets
from app.repositories import Repository, RepositoryError
from app.runs import RunService
from app.schemas import DecisionUpdate, ImportPreview, ImportResult, ManualOverride, Run, RunRequest
from app.storage import now


@pytest.fixture
def manifest():
    return json.loads((REPO_ROOT / "data/demo/manifest.json").read_bytes())


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "text-import.db"
    initialize(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 4
    return Repository(path)


def encode(manifest):
    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def commit(manifest, repo, **kwargs):
    result = import_tickets(encode(manifest), dry_run=False, repository=repo, **kwargs)
    assert isinstance(result, ImportResult)
    return result


def database_snapshot(repo):
    with closing(sqlite3.connect(repo.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return tuple(db.iterdump())


def imported_topic(repo, fixture):
    # Отдельное подготовленное синтетическое состояние с origin=imported.
    manifest = {"dataset_id": "imported-topic", "synthetic": True,
                "tickets": [t.model_dump(mode="json") for t in fixture.sources]}
    commit(manifest, repo)
    for document in fixture.documents:
        source = repo.get_source(document.source_id)
        adjusted = document.model_copy(update={"content_hash": source.content_hash, "version": source.source_version})
        repo.save_source_analysis(adjusted, [n for n in fixture.needs if n.source_id == source.ticket.source_id])
    repo.create_topic(fixture.topic)
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    need = next(n for n in fixture.needs if n.source_id == fixture.sources[0].source_id)
    repo.record_override(ManualOverride(
        override_id="synthetic-manual-attach", source_id=need.source_id, source_version=1,
        need_id=need.need_id, action="attach", topic_id=card.topic.topic_id,
        author="Проверяющий", reason="Сохранить ручную привязку", created_at=now(),
    ), expected_versions={card.topic.topic_id: card.topic.version})
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    repo.record_decision(card.topic.topic_id, DecisionUpdate(
        status="needs_research", owner="Тестовый владелец", reason="Сохранить решение после импорта", version=card.topic.version,
    ))
    return manifest, repo.get_topic(fixture.topic.topic_id, "replay")


def test_first_repeat_update_and_preview_share_one_service(repo, manifest):
    before = database_snapshot(repo)
    preview = import_tickets(encode(manifest), dry_run=True, repository=repo)
    assert isinstance(preview, ImportPreview) and preview.counts.inserted == 4
    assert database_snapshot(repo) == before
    first = import_tickets(encode(manifest), repository=repo)
    assert isinstance(first, ImportResult) and first.counts.inserted == 4
    assert commit(manifest, repo).counts.unchanged == 4
    manifest["tickets"][0]["text"] += " Изменено для синтетической проверки."
    result = commit(manifest, repo)
    assert result.counts.model_dump() == {"inserted": 0, "updated": 1, "unchanged": 3, "invalid": 0}
    reopened = Repository(repo.path)
    assert [s.source_version for s in reopened.get_source_history(manifest["tickets"][0]["source_id"])] == [1, 2]
    assert all(reopened.get_source(t["source_id"]).data_origin == "imported" for t in manifest["tickets"])
    logs = reopened.list_imports()
    assert len(logs) == 3 and len({e.registration_id for e in logs}) == 3
    assert all(UUID(e.registration_id).version == 4 and e.synthetic is True for e in logs)


def test_commit_revalidates_changed_input_after_preview(repo, manifest, monkeypatch):
    assert import_tickets(encode(manifest), dry_run=True, repository=repo).can_commit
    manifest["tickets"][1]["source_id"] = manifest["tickets"][0]["source_id"]
    before = database_snapshot(repo)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input reached import guard")

    monkeypatch.setattr(Repository, "import_commit", forbidden)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo)
    assert exc.value.code == "SOURCE_ID_DUPLICATE" and exc.value.status_code == 422
    assert database_snapshot(repo) == before


def test_commit_uses_actual_counts_after_other_registration(repo, manifest):
    assert import_tickets(encode(manifest), dry_run=True, repository=repo).counts.inserted == 4
    other = copy.deepcopy(manifest)
    other["tickets"] = other["tickets"][:1]
    commit(other, repo)
    result = commit(manifest, repo)
    assert result.counts.inserted == 3 and result.counts.unchanged == 1


def test_exact_request_retry_and_conflict(repo, manifest):
    request_id = str(uuid4())
    first = commit(manifest, repo, registration_id=request_id)
    assert commit(manifest, repo, registration_id=request_id) == first
    assert len(repo.list_imports()) == 1
    manifest["tickets"][0]["text"] += " Другой подготовленный batch."
    before = database_snapshot(repo)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo, registration_id=request_id)
    assert exc.value.code == "REGISTRATION_CONFLICT"
    assert database_snapshot(repo) == before


def test_response_lost_after_commit_is_safe_to_retry(repo, manifest):
    request_id = str(uuid4())
    with pytest.raises(ConnectionError):
        commit(manifest, repo, registration_id=request_id)
        # Сбой доставки результата уже после возврата сервиса, вне его DB-транзакции.
        raise ConnectionError("Synthetic lost response")
    first = repo.list_imports()[0].result
    assert Repository(repo.path).get_source(manifest["tickets"][0]["source_id"]).source_version == 1
    assert commit(manifest, repo, registration_id=request_id) == first
    assert len(repo.list_imports()) == 1
    assert commit(manifest, repo).counts.unchanged == 4
    assert len(repo.list_imports()) == 2


def test_updated_source_preserves_topic_decision_and_history(repo, fixture):
    manifest, saved = imported_topic(repo, fixture)
    source_id = manifest["tickets"][0]["source_id"]
    overrides = repo.get_overrides(source_id)
    changes = repo.topic_changes(saved.topic.topic_id)
    old_text = manifest["tickets"][0]["text"]
    manifest["tickets"][0]["text"] += " Изменённый текст синтетического источника."
    result = commit(manifest, repo)
    assert result.counts.updated == 1 and result.counts.unchanged == 3
    reopened = Repository(repo.path)
    card = reopened.get_topic(saved.topic.topic_id, "replay")
    assert card.topic.topic_id == saved.topic.topic_id and card.history == saved.history
    assert (card.topic.status, card.topic.owner) == (saved.topic.status, saved.topic.owner)
    assert card.topic.version > saved.topic.version and card.stats.ticket_count == 3
    assert card.pending_source_ids == [manifest["tickets"][0]["source_id"]]
    assert reopened.get_overrides(source_id) == [o.model_copy(update={"requires_recheck": True}) for o in overrides]
    assert reopened.topic_changes(saved.topic.topic_id) == changes
    history = reopened.get_source_history(manifest["tickets"][0]["source_id"])
    assert [s.source_version for s in history] == [1, 2] and history[0].ticket.text == old_text
    with pytest.raises(RepositoryError) as exc:
        reopened.record_decision(saved.topic.topic_id, DecisionUpdate(
            status="needs_research", owner="Другой", reason="Устаревшая карточка", version=saved.topic.version,
        ))
    assert exc.value.code == "VERSION_CONFLICT"


def test_log_failure_rolls_back_updates_new_source_and_decision(repo, fixture):
    manifest, saved = imported_topic(repo, fixture)
    overrides = repo.get_overrides(manifest["tickets"][0]["source_id"])
    manifest["tickets"][0]["text"] += " Это изменение должно откатиться."
    extra = copy.deepcopy(manifest["tickets"][1])
    extra["source_id"] = "ROLLBACK-NEW"
    manifest["tickets"].append(extra)
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_log BEFORE INSERT ON import_log BEGIN SELECT RAISE(ABORT,'PRIVATE_SENTINEL'); END")
    before = database_snapshot(repo)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo)
    assert exc.value.code == "IMPORT_DATABASE_UNAVAILABLE" and exc.value.status_code == 503
    assert "PRIVATE_SENTINEL" not in str(exc.value) and exc.value.__suppress_context__
    assert database_snapshot(repo) == before
    assert Repository(repo.path).get_topic(saved.topic.topic_id, "replay") == saved
    assert repo.get_overrides(manifest["tickets"][0]["source_id"]) == overrides
    with pytest.raises(RepositoryError) as missing:
        repo.get_source("ROLLBACK-NEW")
    assert missing.value.code == "SOURCE_NOT_FOUND"


def test_failure_at_sqlite_commit_does_not_return_success(repo, fixture, monkeypatch):
    import app.storage as storage

    manifest, saved = imported_topic(repo, fixture)
    manifest["tickets"][0]["text"] += " Изменение должно откатиться при commit."
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_commit AFTER INSERT ON import_log BEGIN "
                   "INSERT INTO topic_need_links VALUES ('missing-topic','missing-need',1); END")
    before = database_snapshot(repo)
    reached_commit = []

    @contextmanager
    def deferred_connection(path):
        with connection(path) as db:
            # Настоящий SQLite-сбой на commit, после всех INSERT/UPDATE регистрации.
            db.execute("PRAGMA defer_foreign_keys=ON")
            yield db
            if db.execute("PRAGMA foreign_key_check").fetchone():
                reached_commit.append(True)

    monkeypatch.setattr(storage, "connection", deferred_connection)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo)
    assert reached_commit == [True] and exc.value.code == "IMPORT_DATABASE_UNAVAILABLE"
    assert database_snapshot(repo) == before
    assert Repository(repo.path).get_topic(saved.topic.topic_id, "replay") == saved


@pytest.mark.parametrize("synthetic", [True, False])
def test_synthetic_flag_is_honest_and_immutable(repo, manifest, synthetic):
    manifest["synthetic"] = synthetic
    commit(manifest, repo)
    assert repo.get_source(manifest["tickets"][0]["source_id"]).synthetic is synthetic
    assert repo.list_imports()[0].synthetic is synthetic
    manifest["synthetic"] = not synthetic
    before = database_snapshot(repo)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo)
    assert exc.value.code == "SOURCE_PROVENANCE_CONFLICT" and database_snapshot(repo) == before


def test_authored_seed_is_not_converted_to_imported(repo, fixture):
    repo.seed_demo(fixture)
    manifest = {"dataset_id": "wrong-provenance", "synthetic": True,
                "tickets": [t.model_dump(mode="json") for t in fixture.sources]}
    before = database_snapshot(repo)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo)
    assert exc.value.code == "SOURCE_PROVENANCE_CONFLICT" and database_snapshot(repo) == before


@pytest.mark.parametrize("kind", ["bad-json", "duplicate-key", "media", "files", "extra-field"], ids=str)
def test_bad_input_cannot_be_partially_committed(repo, manifest, monkeypatch, kind):
    assets = ()
    raw = encode(manifest)
    if kind == "bad-json":
        raw = b'{"tickets":'
    elif kind == "duplicate-key":
        raw = b'{"tickets":[],"tickets":[]}'
    elif kind == "media":
        manifest["tickets"][0]["assets"] = [{"asset_id": "a-1", "kind": "audio", "path": "audio.wav", "mime_type": "audio/wav"}]
        raw = encode(manifest)
    elif kind == "files":
        assets = (b"synthetic media",)
    else:
        manifest["registration_id"] = str(uuid4())
        raw = encode(manifest)
    before = database_snapshot(repo)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid import reached repository registration")

    monkeypatch.setattr(Repository, "register_dataset", forbidden)
    with pytest.raises(RepositoryError) as exc:
        import_tickets(raw, assets=assets, repository=repo)
    assert exc.value.status_code == 422 and database_snapshot(repo) == before


def test_missing_database_is_not_created(tmp_path, manifest):
    path = tmp_path / "missing-parent" / "missing.db"
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, Repository(path))
    assert exc.value.code == "PREVIEW_DATABASE_MISSING" and not path.parent.exists()


@pytest.mark.parametrize("value", ["", "not-a-uuid", 42, True], ids=["empty", "text", "number", "bool"])
def test_registration_id_is_server_uuid(repo, manifest, value):
    before = database_snapshot(repo)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo, registration_id=value)
    assert exc.value.code == "REGISTRATION_ID_INVALID" and database_snapshot(repo) == before


def test_outer_database_transaction_is_rejected_before_waiting_for_guard(repo, manifest):
    before = database_snapshot(repo)
    with repo.transaction() as tx:
        with pytest.raises(RepositoryError) as exc:
            commit(manifest, tx)
    assert exc.value.code == "IMPORT_TRANSACTION_ACTIVE" and database_snapshot(repo) == before


def test_active_run_blocks_commit_then_guard_is_released(repo, manifest):
    repo.create_run(Run(run_id="active-run", mode="replay", state="queued", stage="queued", created_at=now(), total_materials=0))
    before = database_snapshot(repo)
    with pytest.raises(RepositoryError) as exc:
        commit(manifest, repo)
    assert exc.value.code == "RUN_ACTIVE" and database_snapshot(repo) == before
    repo.interrupt_active_runs()
    assert commit(manifest, repo).counts.inserted == 4


def test_import_guard_is_held_before_registration_and_blocks_run(repo, manifest, monkeypatch):
    entered, release = Event(), Event()
    register = Repository.register_dataset

    def paused(self, batch, **kwargs):
        entered.set()
        assert release.wait(5)
        return register(self, batch, **kwargs)

    monkeypatch.setattr(Repository, "register_dataset", paused)
    service = RunService(Repository(repo.path), "replay", None)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(commit, manifest, repo)
            try:
                assert entered.wait(5)
                with pytest.raises(RepositoryError) as exc:
                    service.start(RunRequest())
                assert exc.value.code == "IMPORT_ACTIVE" and repo.active_run() is None
            finally:
                release.set()
            assert future.result(timeout=10).counts.inserted == 4
    finally:
        service.close()


def test_concurrent_preflights_do_not_reserve_counts_or_hold_db_lock(repo, manifest, monkeypatch):
    barrier = Barrier(2, timeout=5)
    read_counts = Repository.preview_dataset_counts

    def read(self, batch):
        result = read_counts(self, batch)
        barrier.wait()  # Оба снимка чтения закрыты до ожидания guard.
        return result

    monkeypatch.setattr(Repository, "preview_dataset_counts", read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(commit, manifest, Repository(repo.path)) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert sorted((r.counts.inserted, r.counts.unchanged) for r in results) == [(0, 4), (4, 0)]
    assert len(repo.list_imports()) == 2
    assert all(repo.get_source(t["source_id"]).source_version == 1 for t in manifest["tickets"])


def test_commit_does_not_initialize_or_analyze(repo, manifest, monkeypatch):
    import app.db as database
    import app.gateways as gateways

    def forbidden(*args, **kwargs):
        pytest.fail("Text commit called initialization or analysis")

    monkeypatch.setattr(database, "initialize", forbidden)
    for method in ("generate", "transcribe", "describe"):
        monkeypatch.setattr(gateways.UnavailableGateways, method, forbidden)
    for method in ("save_normalized_document", "save_needs", "create_run"):
        monkeypatch.setattr(Repository, method, forbidden)
    assert commit(manifest, repo).counts.inserted == 4
    source = repo.get_source(manifest["tickets"][0]["source_id"])
    assert source.document is None and source.needs == [] and source.requires_analysis
