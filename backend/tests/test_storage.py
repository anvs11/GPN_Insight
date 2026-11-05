import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.db as database
from app.db import SCHEMA, connection, initialize
from app.migrations import execute_statements
from app.config import Settings
from app.main import create_app
from app.repositories import Repository, RepositoryError
from app.schemas import (
    DatasetRegistration, DecisionEvent, DecisionUpdate, EvidenceRef, ExtractedNeed,
    ManualOverride, MaterialError, MaterialInput,
    RegisteredMaterial, ReviewItem, ReviewResolution, Run,
)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "storage.db"
    initialize(path)
    return Repository(path)


def batch(tickets, materials=None, **kwargs):
    return DatasetRegistration(dataset_id="test", product_id=tickets[0].product_id if tickets else "webneft",
                               synthetic=True, origin="authored", tickets=tickets, materials=materials or [], **kwargs)


def decide(repo, topic_id):
    current = repo.get_topic(topic_id, "replay")
    repo.record_decision(topic_id, DecisionUpdate(status="needs_research", owner="Владелец", reason="Подтверждены источники", version=current.topic.version))
    return repo.get_topic(topic_id, "replay")


def seed_v1(path, fixture):
    with connection(path) as db:
        execute_statements(db, SCHEMA)
        for ticket, document in zip(fixture.sources, fixture.documents, strict=True):
            db.execute("INSERT INTO sources VALUES (?,?,?)", (ticket.source_id, ticket.model_dump_json(), document.model_dump_json()))
        for need in fixture.needs:
            db.execute("INSERT INTO needs VALUES (?,?,?)", (need.need_id, need.source_id, need.model_dump_json()))
        topic = fixture.topic.model_copy(update={"version": 2, "status": "needs_research", "owner": "Владелец v1"})
        db.execute("INSERT INTO topics VALUES (?,?)", (topic.topic_id, topic.model_dump_json()))
        db.executemany("INSERT INTO topic_needs VALUES (?,?)", [(topic.topic_id, n) for n in topic.need_ids])
        event = DecisionEvent(event_id="preserved-event", topic_id=topic.topic_id, previous_status="new", new_status="needs_research",
                              owner="Владелец v1", reason="Исходное решение", occurred_at=datetime.now(timezone.utc), topic_version=2)
        db.execute("INSERT INTO decision_events VALUES (?,?,?,?)", (event.event_id, topic.topic_id, 2, event.model_dump_json()))
        db.execute("INSERT INTO demo_fixtures VALUES (?,?)", (fixture.fixture_id, fixture.content_hash()))
    return event


def test_migration_preserves_v1_and_safe_repeat(tmp_path, fixture):
    path = tmp_path / "v1.db"
    event = seed_v1(path, fixture)
    result = initialize(path)
    assert result.version == 3 and result.migrated_from == 1 and result.backup_path.exists()
    with connection(result.backup_path) as backup:
        assert backup.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert backup.execute("SELECT body_json FROM decision_events").fetchone()[0] == event.model_dump_json()
    assert initialize(path).backup_path is None
    repo = Repository(path)
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    assert card.topic.version == 2 and card.topic.owner == "Владелец v1"
    assert card.history == [event] and card.stats.ticket_count == 4
    assert repo.get_source_history(fixture.sources[0].source_id)[0].source_version == 1
    assert repo.seed_demo(fixture) is False
    with connection(path) as db:
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 4
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchone() is None


def test_migration_failure_rolls_back_ddl_and_data(tmp_path, fixture, monkeypatch):
    path = tmp_path / "v1.db"
    event = seed_v1(path, fixture)
    original = database.migrate_v1
    def fail_after_copy(db):
        original(db)
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(database, "migrate_v1", fail_after_copy)
    with pytest.raises(RuntimeError, match="injected"):
        initialize(path)
    with connection(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert db.execute("SELECT body_json FROM decision_events").fetchone()[0] == event.model_dump_json()
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='source_records'").fetchone() is None
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_unknown_schema_is_not_modified(tmp_path):
    path = tmp_path / "foreign.db"
    with connection(path) as db:
        db.execute("CREATE TABLE schema_version(version INTEGER)")
        db.execute("INSERT INTO schema_version VALUES (99)")
        db.execute("CREATE TABLE preserved(value TEXT)")
        db.execute("INSERT INTO preserved VALUES ('keep')")
    with pytest.raises(RuntimeError):
        initialize(path)
    with connection(path) as db:
        assert db.execute("SELECT value FROM preserved").fetchone()[0] == "keep"
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 99


def test_register_repeat_versions_and_reopen(repo, fixture):
    result = repo.register_dataset(batch(fixture.sources))
    assert result.counts.inserted == 4
    repeated = Repository(repo.path).register_dataset(batch(fixture.sources))
    assert repeated.counts.unchanged == 4 and repeated.counts.inserted == 0
    first = repo.get_source(fixture.sources[0].source_id)
    assert first.source_version == 1 and first.document is None and first.requires_analysis
    assert len(repo.get_source_history(first.ticket.source_id)) == 1
    assert len(repo.list_imports()) == 2
    assert all(log.result.counts.invalid == 0 for log in repo.list_imports())
    assert repo.registered_materials() == []


def test_source_identity_includes_product(repo, fixture):
    one = fixture.sources[0]
    other = one.model_copy(update={"product_id": "synthetic-other-product"})
    repo.register_dataset(batch([one]))
    repo.register_dataset(batch([other]))
    assert repo.get_source(one.source_id).ticket.product_id == "webneft"
    assert repo.get_source(one.source_id, other.product_id).ticket.product_id == other.product_id
    assert len(repo.get_source_history(one.source_id)) == 1


def test_registration_and_log_rollback(repo, fixture):
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_log BEFORE INSERT ON import_log BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.register_dataset(batch(fixture.sources))
    with connection(repo.path) as db:
        for table in ("source_records", "source_versions", "import_log"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_explicit_transaction_rolls_back_all_operations(repo, fixture):
    with pytest.raises(RuntimeError):
        with repo.transaction() as tx:
            tx.register_dataset(batch(fixture.sources))
            for doc in fixture.documents:
                tx.save_source_analysis(doc, [n for n in fixture.needs if n.source_id == doc.source_id])
            tx.create_topic(fixture.topic)
            raise RuntimeError("cancel")
    assert repo.list_imports() == [] and repo.list_topic_ids(None) == []
    with pytest.raises(RepositoryError):
        repo.get_source(fixture.sources[0].source_id)


def test_same_registration_id_is_idempotent(repo, fixture):
    request = batch(fixture.sources)
    initial = repo.register_dataset(request, registration_id="server-owned-request")
    assert repo.register_dataset(request, registration_id="server-owned-request") == initial
    assert len(repo.list_imports()) == 1
    different = request.model_copy(update={"dataset_id": "other"})
    with pytest.raises(RepositoryError, match="другого запроса"):
        repo.register_dataset(different, registration_id="server-owned-request")


def test_update_source_keeps_history_decision_and_id(repo, fixture):
    repo.seed_demo(fixture)
    saved = decide(repo, fixture.topic.topic_id)
    changed = fixture.sources[0].model_copy(update={"text": "Изменённый синтетический запрос"})
    result = repo.register_dataset(batch([changed]))
    assert result.counts.updated == 1
    reopened = Repository(repo.path)
    history = reopened.get_source_history(changed.source_id)
    assert [h.source_version for h in history] == [1, 2]
    assert history[0].ticket.text == fixture.sources[0].text and history[1].ticket.text == changed.text
    assert history[1].requires_analysis and history[1].needs == []
    card = reopened.get_topic(fixture.topic.topic_id, "replay")
    assert card.topic.topic_id == saved.topic.topic_id and card.topic.owner == saved.topic.owner
    assert card.topic.status == saved.topic.status and card.history == saved.history
    assert card.stats.ticket_count == 3 and card.pending_source_ids == [changed.source_id]
    assert card.topic.version > saved.topic.version
    with pytest.raises(RepositoryError, match="изменилась|изменена"):
        reopened.record_decision(card.topic.topic_id, DecisionUpdate(status="needs_research", owner="Другой", reason="Старая карточка", version=saved.topic.version))


def test_material_history_and_hash_does_not_use_server_path(repo, fixture):
    ticket = fixture.sources[0].model_copy(update={"assets": [MaterialInput(asset_id="audio-1", kind="audio", path="call.wav", mime_type="audio/wav")]})
    material = RegisteredMaterial(asset_id="audio-1", kind="audio", content_hash="a" * 64, storage_path="server-uuid.wav", original_filename="call.wav", mime_type="audio/wav", byte_size=123)
    assert repo.register_dataset(batch([ticket], [material])).counts.inserted == 1
    redundant = material.model_copy(update={"storage_path": "unused-prepared-file.wav"})
    assert repo.register_dataset(batch([ticket], [redundant])).counts.unchanged == 1
    changed = material.model_copy(update={"content_hash": "b" * 64, "storage_path": "server-new-uuid.wav"})
    assert repo.register_dataset(batch([ticket], [changed])).counts.updated == 1
    assert [m.storage_path for m in repo.source_materials(ticket.source_id, version=1)] == [material.storage_path]
    assert [m.storage_path for m in repo.source_materials(ticket.source_id)] == [changed.storage_path]
    assert len(repo.registered_materials()) == 2
    assert not (repo.path.parent / material.storage_path).exists()


def test_empty_needs_and_invalid_quote_do_not_hide_errors(repo, fixture):
    repo.register_dataset(batch([fixture.sources[0]]))
    doc = fixture.documents[0]
    repo.save_normalized_document(doc)
    invalid = fixture.needs[0].model_copy(update={"evidence_quote": "Выдуманная цитата"})
    with pytest.raises(RepositoryError, match="не подтверждена"):
        repo.save_needs("webneft", doc.source_id, 1, [invalid])
    assert repo.get_source(doc.source_id).requires_analysis
    repo.save_needs("webneft", doc.source_id, 1, [])
    assert not repo.get_source(doc.source_id).requires_analysis
    assert repo.get_source(doc.source_id).needs == []


def test_distinct_sources_and_decision_survive_new_evidence(repo, fixture):
    repo.seed_demo(fixture)
    saved = decide(repo, fixture.topic.topic_id)
    source = fixture.sources[0].model_copy(update={"source_id": "new-source"})
    repo.register_dataset(batch([source]))
    detail = repo.get_source(source.source_id)
    doc = fixture.documents[0].model_copy(update={"source_id": source.source_id, "content_hash": detail.content_hash,
        "fragments": [fixture.documents[0].fragments[0].model_copy(update={"fragment_id": "new-fragment"})]})
    needs = [fixture.needs[0].model_copy(update={"source_id": source.source_id, "need_id": "new-need-1", "fragment_id": "new-fragment"}),
             fixture.needs[0].model_copy(update={"source_id": source.source_id, "need_id": "new-need-2", "fragment_id": "new-fragment"})]
    repo.save_source_analysis(doc, needs)
    repo.attach_needs(saved.topic.topic_id, [n.need_id for n in needs], expected_version=saved.topic.version)
    card = Repository(repo.path).get_topic(saved.topic.topic_id, "replay")
    assert card.stats.ticket_count == 5 and len(card.topic.need_ids) == 6
    assert card.history == saved.history and card.topic.owner == saved.topic.owner
    repo.attach_needs(card.topic.topic_id, [n.need_id for n in needs], expected_version=card.topic.version)
    assert repo.get_topic(card.topic.topic_id, "replay").stats.ticket_count == 5


def test_passport_history_and_catalog_revision(repo, fixture):
    repo.seed_demo(fixture)
    saved = decide(repo, fixture.topic.topic_id)
    revision = repo.catalog_snapshot().catalog_revision
    passport = saved.topic.passport.model_copy(update={"version": 2, "scope_confirmed": True})
    repo.update_passport(saved.topic.topic_id, passport, expected_version=saved.topic.version, author="Аналитик", reason="Уточнены границы", title="Новое название той же темы")
    current = repo.get_topic(saved.topic.topic_id, "replay")
    assert current.topic.topic_id == saved.topic.topic_id and current.history == saved.history
    assert current.topic.passport.scope_confirmed and current.topic.title.startswith("Новое")
    assert repo.catalog_snapshot().catalog_revision == revision + 1
    assert repo.topic_changes(current.topic.topic_id)[-1].before["passport"]["version"] == 1


def test_manual_exclusion_is_not_reversed_and_becomes_stale(repo, fixture):
    repo.seed_demo(fixture)
    need = fixture.needs[0]
    review = ReviewItem(review_id="review-1", version=1, kind="assignment", need_ids=[need.need_id], reason="Проверить", evidence_refs=[fixture.topic.evidence[0]])
    repo.save_review(review)
    override = ManualOverride(override_id="manual-1", source_id=need.source_id, source_version=1, need_id=need.need_id,
                              action="exclude", topic_id=fixture.topic.topic_id, author="Аналитик", reason="Неверная связь", created_at=datetime.now(timezone.utc))
    repo.record_override(override, expected_versions={fixture.topic.topic_id: 1})
    repo.record_override(override, expected_versions={fixture.topic.topic_id: 1})
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    assert card.stats.ticket_count == 3
    with pytest.raises(RepositoryError, match="ручным решением"):
        repo.attach_needs(card.topic.topic_id, [need.need_id], expected_version=card.topic.version)
    repo.save_needs("webneft", need.source_id, 1, [need])
    assert repo.get_topic(card.topic.topic_id, "replay").stats.ticket_count == 3
    repo.register_dataset(batch([fixture.sources[0].model_copy(update={"text": "Новый текст"})]))
    assert repo.get_overrides(need.source_id)[0].requires_recheck
    assert repo.get_review(review.review_id).requires_recheck
    assert repo.get_source_history(need.source_id)[0].needs == [need]


def test_review_resolution_storage_is_versioned(repo, fixture):
    repo.seed_demo(fixture)
    need = fixture.needs[0]
    review = ReviewItem(review_id="review", version=1, kind="assignment", need_ids=[need.need_id], reason="Неясно", evidence_refs=[fixture.topic.evidence[0]])
    repo.save_review(review)
    resolution = ReviewResolution(action="insufficient_data", reason="Недостаточно контекста", author="Аналитик", resolved_at=datetime.now(timezone.utc))
    resolved = review.model_copy(update={"version": 2, "status": "insufficient_data", "resolution": resolution})
    repo.save_review(resolved, expected_version=1)
    repo.save_review(resolved, expected_version=1)
    assert repo.review_history("review") == [review, resolved]
    assert Repository(repo.path).get_review("review") == resolved
    with pytest.raises(RepositoryError):
        repo.save_review(resolved.model_copy(update={"version": 3}), expected_version=1)
    assert repo.list_reviews() == []


def test_run_singleton_restart_and_import_lock(repo, fixture):
    created = datetime.now(timezone.utc)
    run = Run(run_id="run-1", mode="replay", state="queued", stage="normalization", created_at=created)
    assert repo.create_run(run) == run
    assert repo.create_run(run.model_copy(update={"run_id": "run-2"})).run_id == "run-1"
    with pytest.raises(RepositoryError, match="активного запуска"):
        repo.register_dataset(batch(fixture.sources))
    running = Run.model_validate({**run.model_dump(), "state": "running", "started_at": created})
    repo.save_run(running)
    assert Repository(repo.path).interrupt_active_runs() == 1
    assert repo.get_run("run-1").state == "interrupted"
    assert repo.interrupt_active_runs() == 0
    repo.register_dataset(batch(fixture.sources))
    assert repo.create_run(run.model_copy(update={"run_id": "run-2"})).run_id == "run-2"


def test_business_decisions_are_stored_without_model_calls(repo, fixture):
    repo.seed_demo(fixture)
    topic_id = fixture.topic.topic_id
    for status, fields in [("deferred", {"review_after": "2020-01-01"}), ("declined", {}),
                           ("implemented", {"implemented_at": "2026-10-02T00:00:00+03:00", "implementation_comment": "Синтетический результат"})]:
        before = repo.get_topic(topic_id, "replay")
        update = DecisionUpdate(status=status, owner="Владелец", reason="Решение человека", version=before.topic.version, **fields)
        repo.record_decision(topic_id, update)
        current = Repository(repo.path).get_topic(topic_id, "replay")
        assert current.topic.status == status
        if status == "deferred":
            assert current.stats.review_due
    assert current.stats.post_implementation_ticket_count == 3
    assert len(current.history) == 3


def test_partial_normalization_is_visible_and_retryable(repo, fixture):
    ticket, valid = fixture.sources[0], fixture.documents[0]
    repo.register_dataset(batch([ticket]))
    partial = valid.model_copy(update={"fragments": [], "errors": [MaterialError(source_id=ticket.source_id, stage="normalize", code="MODEL_TIMEOUT", message="Материал не обработан")]})
    repo.save_source_analysis(partial, [])
    assert repo.get_source(ticket.source_id).requires_analysis
    assert repo.get_source(ticket.source_id).document.errors[0].code == "MODEL_TIMEOUT"
    repo.save_source_analysis(valid, [fixture.needs[0]])
    assert not repo.get_source(ticket.source_id).requires_analysis
    assert repo.normalization_history(ticket.source_id) == [partial, valid]
    changed = valid.model_copy(update={"normalization_version": "different"})
    with pytest.raises(RepositoryError, match="перезапись запрещена"):
        repo.save_normalized_document(changed)
    assert repo.get_source(ticket.source_id).document == valid


def test_override_rejects_stale_topic_version(repo, fixture):
    repo.seed_demo(fixture)
    saved = decide(repo, fixture.topic.topic_id)
    need = fixture.needs[0]
    override = ManualOverride(override_id="stale-manual", source_id=need.source_id, source_version=1, need_id=need.need_id,
                              action="exclude", topic_id=saved.topic.topic_id, author="Аналитик", reason="Проверка версии", created_at=datetime.now(timezone.utc))
    with pytest.raises(RepositoryError, match="изменилась"):
        repo.record_override(override, expected_versions={saved.topic.topic_id: 1})
    assert repo.get_overrides(need.source_id) == []
    assert repo.get_topic(saved.topic.topic_id, "replay").stats.ticket_count == 4


def test_savepoint_keeps_failed_registration_atomic_when_error_is_caught(repo, fixture):
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_third BEFORE INSERT ON source_versions WHEN new.source_id='DEMO-T003' BEGIN SELECT RAISE(ABORT,'fail'); END")
    with repo.transaction() as tx:
        with pytest.raises(sqlite3.IntegrityError):
            tx.register_dataset(batch(fixture.sources))
        tx.register_dataset(batch([]))
    assert len(repo.list_imports()) == 1
    with connection(repo.path) as db:
        assert db.execute("SELECT count(*) FROM source_records").fetchone()[0] == 0


def test_registration_failure_preserves_existing_decision_and_links(repo, fixture):
    repo.seed_demo(fixture)
    saved = decide(repo, fixture.topic.topic_id)
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_log BEFORE INSERT ON import_log BEGIN SELECT RAISE(ABORT,'fail'); END")
    changed = fixture.sources[0].model_copy(update={"text": "Изменение, которое не должно сохраниться"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.register_dataset(batch([changed]))
    assert repo.get_source(changed.source_id).source_version == 1
    assert Repository(repo.path).get_topic(fixture.topic.topic_id, "replay") == saved


def test_concurrent_registration_does_not_duplicate_sources(repo, fixture):
    request = batch(fixture.sources)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: Repository(repo.path).register_dataset(request), range(2)))
    assert sorted(r.counts.inserted for r in results) == [0, 4]
    assert sorted(r.counts.unchanged for r in results) == [0, 4]
    assert all(len(repo.get_source_history(s.source_id)) == 1 for s in fixture.sources)


def test_manual_reassignment_keeps_decisions_of_both_topics(repo, fixture):
    repo.seed_demo(fixture)
    source = fixture.sources[0].model_copy(update={"source_id": "other-source"})
    repo.register_dataset(batch([source]))
    stored = repo.get_source(source.source_id)
    doc = fixture.documents[0].model_copy(update={"source_id": source.source_id, "content_hash": stored.content_hash,
        "fragments": [fixture.documents[0].fragments[0].model_copy(update={"fragment_id": "other-fragment"})]})
    need = fixture.needs[0].model_copy(update={"source_id": source.source_id, "need_id": "other-need", "fragment_id": "other-fragment"})
    repo.save_source_analysis(doc, [need])
    target = fixture.topic.model_copy(update={"topic_id": "target-topic", "need_ids": [need.need_id],
        "evidence": [EvidenceRef(source_id=source.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)]})
    repo.create_topic(target)
    one = decide(repo, fixture.topic.topic_id)
    two = decide(repo, target.topic_id)
    moved = fixture.needs[0]
    override = ManualOverride(override_id="reassign", source_id=moved.source_id, source_version=1, need_id=moved.need_id,
                              action="attach", topic_id=target.topic_id, author="Аналитик", reason="Ручное переназначение", created_at=datetime.now(timezone.utc))
    repo.record_override(override, expected_versions={one.topic.topic_id: one.topic.version, two.topic.topic_id: two.topic.version})
    assert repo.get_topic(one.topic.topic_id, "replay").stats.ticket_count == 3
    assert repo.get_topic(two.topic.topic_id, "replay").stats.ticket_count == 2
    assert repo.get_topic(one.topic.topic_id, "replay").history == one.history
    assert repo.get_topic(two.topic.topic_id, "replay").history == two.history
    assert moved.need_id in repo.topic_changes(two.topic.topic_id)[-1].after["need_ids"]


def test_repository_expected_versions_do_not_accept_booleans(repo, fixture):
    repo.seed_demo(fixture)
    with pytest.raises(RepositoryError):
        repo.attach_needs(fixture.topic.topic_id, [], expected_version=True)
    with pytest.raises(RepositoryError):
        repo.update_passport(fixture.topic.topic_id, fixture.topic.passport.model_copy(update={"version": 2}), expected_version=True, author="Тест", reason="Тип версии")
    with pytest.raises(RepositoryError):
        repo.save_needs("webneft", fixture.sources[0].source_id, True, [fixture.needs[0]])
    assert repo.get_topic(fixture.topic.topic_id, "replay").topic.version == 1


def test_only_app_start_interrupts_unfinished_runs(repo):
    run = Run(run_id="interrupted-by-start", mode="replay", state="queued", stage="normalization", created_at=datetime.now(timezone.utc))
    repo.create_run(run)
    initialize(repo.path)
    assert repo.get_run(run.run_id).state == "queued"
    with TestClient(create_app(Settings(_env_file=None, app_db_path=repo.path)), base_url="http://127.0.0.1") as client:
        assert repo.get_run(run.run_id).state == "interrupted"
        assert client.get("/api/v1/health").json()["processing_state"] == "idle"


def test_manual_creation_after_exclusion_preserves_automatic_block(repo, fixture):
    repo.seed_demo(fixture)
    need = fixture.needs[0]
    repo.record_override(ManualOverride(override_id="exclude-before-create", source_id=need.source_id, source_version=1,
        need_id=need.need_id, action="exclude", topic_id=fixture.topic.topic_id, author="Аналитик", reason="Ошибочная связь", created_at=datetime.now(timezone.utc)),
        expected_versions={fixture.topic.topic_id: 1})
    new_topic = fixture.topic.model_copy(update={"topic_id": "manual-new-topic", "need_ids": [need.need_id], "evidence": [fixture.topic.evidence[0]]})
    with pytest.raises(RepositoryError, match="переопределение"):
        repo.create_topic(new_topic)
    with pytest.raises(RepositoryError, match="автора и причины"):
        repo.create_topic(new_topic, manual_author="Аналитик", manual_reason=" ")
    repo.create_topic(new_topic, manual_author="Аналитик", manual_reason="Создана отдельная тема после review")
    card = repo.get_topic(new_topic.topic_id, "replay")
    assert card.stats.ticket_count == 1
    assert repo.get_topic(fixture.topic.topic_id, "replay").stats.ticket_count == 3
    overrides = repo.get_overrides(need.source_id)
    assert [o.action for o in overrides] == ["exclude", "attach"]
    assert overrides[-1].topic_id == new_topic.topic_id
    repo.attach_needs(new_topic.topic_id, [need.need_id], expected_version=card.topic.version)
    with pytest.raises(RepositoryError, match="ручным решением"):
        old = repo.get_topic(fixture.topic.topic_id, "replay")
        repo.attach_needs(old.topic.topic_id, [need.need_id], expected_version=old.topic.version)


def test_run_cannot_hide_unprocessed_materials_by_reducing_total(repo):
    created = datetime.now(timezone.utc)
    run = Run(run_id="coverage", mode="replay", state="queued", stage="normalization", created_at=created,
              total_materials=3, coverage=0)
    repo.create_run(run)
    running = Run.model_validate({**run.model_dump(), "state": "running", "started_at": created})
    repo.save_run(running)
    falsely_completed = Run.model_validate({**running.model_dump(), "state": "completed", "total_materials": 0,
        "coverage": None, "finished_at": datetime.now(timezone.utc)})
    with pytest.raises(RepositoryError):
        repo.save_run(falsely_completed)
    assert repo.get_run(run.run_id) == running


def test_normalization_rejects_asr_from_image(repo, fixture):
    image = MaterialInput(asset_id="image", kind="image", path="image.png", mime_type="image/png")
    ticket = fixture.sources[0].model_copy(update={"assets": [image]})
    material = RegisteredMaterial(asset_id="image", kind="image", original_filename="image.png", storage_path="server-image.png",
                                  content_hash="c" * 64, mime_type="image/png", byte_size=123)
    repo.register_dataset(batch([ticket], [material]))
    source = repo.get_source(ticket.source_id)
    fragment = fixture.documents[0].fragments[0].model_copy(update={"kind": "asr", "asset_id": image.asset_id})
    invalid = fixture.documents[0].model_copy(update={"content_hash": source.content_hash, "fragments": [fragment]})
    with pytest.raises(RepositoryError):
        repo.save_normalized_document(invalid)
    assert repo.get_source(ticket.source_id).document is None
    assert repo.normalization_history(ticket.source_id) == []
    unknown_error = fixture.documents[0].model_copy(update={"content_hash": source.content_hash, "errors": [
        MaterialError(source_id=ticket.source_id, asset_id="unknown", stage="normalize", code="FAILED", message="Неверный ID материала")]})
    with pytest.raises(RepositoryError, match="незарегистрированный"):
        repo.save_normalized_document(unknown_error)


def test_topic_cannot_mislabel_synthetic_evidence_as_real(repo, fixture):
    repo.register_dataset(batch(fixture.sources))
    for doc in fixture.documents:
        repo.save_source_analysis(doc, [n for n in fixture.needs if n.source_id == doc.source_id])
    with pytest.raises(RepositoryError):
        repo.create_topic(fixture.topic, synthetic=False)
    assert repo.list_topic_ids(None) == []
    repo.create_topic(fixture.topic)
    assert repo.get_topic(fixture.topic.topic_id, "replay").synthetic
    # Ошибочный старый флаг не превращает исторические синтетические источники в реальные.
    with connection(repo.path) as db:
        db.execute("UPDATE topics SET synthetic=0 WHERE topic_id=?", (fixture.topic.topic_id,))
        db.execute("UPDATE topic_need_links SET active=0 WHERE topic_id=?", (fixture.topic.topic_id,))
    assert repo.get_topic(fixture.topic.topic_id, "replay").synthetic


def test_topic_marks_mixed_evidence_as_synthetic(repo, fixture):
    real = batch([fixture.sources[0]]).model_copy(update={"synthetic": False})
    repo.register_dataset(real)
    repo.save_source_analysis(fixture.documents[0], [fixture.needs[0]])
    topic = fixture.topic.model_copy(update={"need_ids": [fixture.needs[0].need_id], "evidence": [fixture.topic.evidence[0]]})
    repo.create_topic(topic)
    assert not repo.get_topic(topic.topic_id, "replay").synthetic
    repo.register_dataset(batch([fixture.sources[1]]))
    repo.save_source_analysis(fixture.documents[1], [fixture.needs[1]])
    repo.attach_needs(topic.topic_id, [fixture.needs[1].need_id], expected_version=1)
    assert repo.get_topic(topic.topic_id, "replay").synthetic


def test_manual_topic_creation_rolls_back_on_override_failure(repo, fixture):
    repo.seed_demo(fixture)
    first = fixture.needs[0]
    repo.record_override(ManualOverride(override_id="excluded", source_id=first.source_id, source_version=1, need_id=first.need_id,
        topic_id=fixture.topic.topic_id, action="exclude", author="Аналитик", reason="На проверку", created_at=datetime.now(timezone.utc)),
        expected_versions={fixture.topic.topic_id: 1})
    new_topic = fixture.topic.model_copy(update={"topic_id": "rollback-manual", "need_ids": [first.need_id], "evidence": [fixture.topic.evidence[0]]})
    before = repo.get_topic(fixture.topic.topic_id, "replay")
    revision = repo.catalog_snapshot().catalog_revision
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_attach_override BEFORE INSERT ON manual_overrides WHEN new.action='attach' BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_topic(new_topic, manual_author="Аналитик", manual_reason="Ручная тема")
    with pytest.raises(RepositoryError):
        repo.get_topic(new_topic.topic_id, "replay")
    assert repo.get_topic(fixture.topic.topic_id, "replay") == before
    assert repo.catalog_snapshot().catalog_revision == revision
    assert [o.action for o in repo.get_overrides(first.source_id)] == ["exclude"]
