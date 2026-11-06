"""Прямые вызовы текстового сервиса на отдельной синтетической SQLite v3."""

import copy
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from app.config import REPO_ROOT
from app.db import connection, initialize
from app.demo import DemoFixture, load_fixture
from app.ingestion.importer import import_tickets
from app.repositories import Repository, RepositoryError
from app.schemas import DecisionUpdate, ImportResult, ManualOverride, Run
from app.storage import now


def check_related_topic(folder, fixture: DemoFixture) -> dict:
    """Проверить именно карточку изменяемого imported-источника, отдельно от authored seed."""
    repo = Repository(folder / "related-topic.db")
    initialize(repo.path)
    manifest = {"dataset_id": "synthetic-related-topic", "synthetic": True,
                "tickets": [t.model_dump(mode="json") for t in fixture.sources]}
    assert import_tickets(json.dumps(manifest), repository=repo).counts.inserted == 4
    # Только подготовка проверки: готовые синтетические документы/потребности из фикстуры.
    # Сам сервис импорта их не создаёт и не вызывает нормализатор/модель.
    for document in fixture.documents:
        source = repo.get_source(document.source_id)
        adjusted = document.model_copy(update={"content_hash": source.content_hash, "version": source.source_version})
        repo.save_source_analysis(adjusted, [n for n in fixture.needs if n.source_id == document.source_id])
    repo.create_topic(fixture.topic)
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    source_id = fixture.sources[0].source_id
    need = next(n for n in fixture.needs if n.source_id == source_id)
    repo.record_override(ManualOverride(
        override_id="synthetic-manual-attach", source_id=source_id, source_version=1,
        need_id=need.need_id, action="attach", topic_id=card.topic.topic_id,
        author="Проверяющий", reason="Сохранить ручную привязку", created_at=now(),
    ), expected_versions={card.topic.topic_id: card.topic.version})
    card = repo.get_topic(card.topic.topic_id, "replay")
    repo.record_decision(card.topic.topic_id, DecisionUpdate(
        status="needs_research", owner="Проверяющий", reason="Сохранить решение связанного источника",
        version=card.topic.version,
    ))
    saved = repo.get_topic(card.topic.topic_id, "replay")
    changes = repo.topic_changes(card.topic.topic_id)
    overrides = repo.get_overrides(source_id)
    old_text = manifest["tickets"][0]["text"]
    manifest["tickets"][0]["text"] += " Дополнение для проверки связанной карточки."
    result = import_tickets(json.dumps(manifest), repository=repo)
    assert (result.counts.updated, result.counts.unchanged) == (1, 3)
    reopened = Repository(repo.path)
    card = reopened.get_topic(saved.topic.topic_id, "replay")
    assert (card.topic.topic_id, card.topic.status, card.topic.owner, card.history) == (
        saved.topic.topic_id, saved.topic.status, saved.topic.owner, saved.history,
    )
    assert card.topic.version > saved.topic.version and card.stats.ticket_count == 3
    assert card.pending_source_ids == [source_id] and reopened.topic_changes(card.topic.topic_id) == changes
    assert reopened.get_overrides(source_id) == [o.model_copy(update={"requires_recheck": True}) for o in overrides]
    versions = reopened.get_source_history(source_id)
    assert [s.source_version for s in versions] == [1, 2] and versions[0].ticket.text == old_text
    try:
        reopened.record_decision(card.topic.topic_id, DecisionUpdate(
            status="needs_research", owner="Другой", reason="Устаревшая версия", version=saved.topic.version,
        ))
    except RepositoryError as exc:
        assert exc.code == "VERSION_CONFLICT"
    else:
        raise AssertionError("Stale decision was accepted")
    print("related-topic: same_id=true; decision/history/manual_override=preserved; "
          "active=3 pending=1; stale-decision=VERSION_CONFLICT")
    return {"case": "related-topic", "same_id": True, "decision_preserved": True,
            "history_preserved": True, "manual_override_requires_recheck": True,
            "active_ticket_count": 3, "pending_source_count": 1, "stale_decision": "VERSION_CONFLICT"}


def main() -> None:
    folder = REPO_ROOT / "var" / ("text-import-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    repo = Repository(folder / "check.db")
    # Только подготовка отдельной базы проверки; сам import_tickets не делает init-db.
    initialize(repo.path)
    fixture = load_fixture(REPO_ROOT / "data/contracts/demo_state.json")
    repo.seed_demo(fixture)
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    repo.record_decision(card.topic.topic_id, DecisionUpdate(
        status="needs_research", owner="Проверяющий", reason="Проверка текстовой фиксации", version=card.topic.version,
    ))
    saved = repo.get_topic(card.topic.topic_id, "replay")
    manifest = json.loads((REPO_ROOT / "data/demo/manifest.json").read_bytes())
    observations = []

    def snapshot():
        with closing(sqlite3.connect(repo.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            return tuple(db.iterdump())

    def commit(value, label, expected, **kwargs):
        result = import_tickets(json.dumps(value, ensure_ascii=False), dry_run=False, repository=repo, **kwargs)
        assert isinstance(result, ImportResult)
        assert (result.counts.inserted, result.counts.updated, result.counts.unchanged) == expected
        assert repo.get_topic(saved.topic.topic_id, "replay") == saved
        print(f"{label}: inserted={result.counts.inserted} updated={result.counts.updated} unchanged={result.counts.unchanged}")
        observations.append({"case": label, "counts": result.counts.model_dump()})
        return result

    def failure(value, label, code):
        before = snapshot()
        try:
            import_tickets(json.dumps(value, ensure_ascii=False), dry_run=False, repository=repo)
        except RepositoryError as exc:
            assert exc.code == code
        else:
            raise AssertionError("Expected import failure")
        assert snapshot() == before
        assert repo.get_topic(saved.topic.topic_id, "replay") == saved
        print(f"{label}: {code}; state_unchanged=true")
        observations.append({"case": label, "code": code, "state_unchanged": True})

    print("synthetic=true; operation=text-import")
    before = snapshot()
    preview = import_tickets(json.dumps(manifest, ensure_ascii=False), dry_run=True, repository=repo)
    assert preview.can_commit and preview.counts.inserted == 4 and snapshot() == before
    print("preview: inserted=4; database_unchanged=true")
    request_id = str(uuid4())
    first = commit(manifest, "first", (4, 0, 0), registration_id=request_id)
    logs = repo.list_imports()
    retried = import_tickets(json.dumps(manifest), dry_run=False, repository=repo, registration_id=request_id)
    assert retried == first and repo.list_imports() == logs
    print("exact-retry: same_receipt=true; new_import_log=false")
    commit(manifest, "repeat", (0, 0, 4))
    manifest["tickets"][0]["text"] += " Дополнение синтетического обращения."
    commit(manifest, "update", (0, 1, 3))

    changed = copy.deepcopy(manifest)
    changed["tickets"][0]["text"] += " Изменение должно откатиться."
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER check_fail_log BEFORE INSERT ON import_log BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    failure(changed, "rollback", "IMPORT_DATABASE_UNAVAILABLE")
    with connection(repo.path) as db:
        db.execute("DROP TRIGGER check_fail_log")
    media = copy.deepcopy(manifest)
    media["tickets"][0]["assets"] = [{"asset_id": "a-1", "kind": "audio", "path": "call.wav", "mime_type": "audio/wav"}]
    failure(media, "media", "MEDIA_NOT_SUPPORTED")
    repo.create_run(Run(run_id="synthetic-active-run", mode="replay", state="queued", stage="queued", created_at=now(), total_materials=0))
    failure(manifest, "active-run", "RUN_ACTIVE")
    repo.interrupt_active_runs()

    reopened = Repository(repo.path)
    versions = [s.source_version for s in reopened.get_source_history(manifest["tickets"][0]["source_id"])]
    assert versions == [1, 2] and reopened.get_topic(saved.topic.topic_id, "replay") == saved
    assert saved.stats.ticket_count == 4
    observations.append(check_related_topic(folder, fixture))
    report = folder / "result.json"
    report.write_text(json.dumps({"synthetic": True, "observations": observations, "source_versions": versions,
                                  "decision_preserved": True, "history_preserved": True, "ticket_count": 4},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: source versions=[1, 2]; decision/history/count=4 preserved after reopen")
    print(f"report: {report.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
