"""Повторяемая ручная проверка U1-01 на новой синтетической БД в var/."""

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from app.config import REPO_ROOT
from app.db import initialize
from app.demo import load_fixture
from app.ingestion.importer import import_tickets
from app.repositories import Repository
from app.schemas import DatasetRegistration, DecisionUpdate


def file_snapshot(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file()}


def main() -> None:
    # Каждый запуск готовит собственные данные; рабочая БД и .env не используются.
    folder = REPO_ROOT / "var" / ("preview-review-" + uuid4().hex)
    folder.mkdir(parents=True)
    path = folder / "check.db"
    initialize(path)
    repo = Repository(path)
    fixture = load_fixture(REPO_ROOT / "data/contracts/demo_state.json")
    repo.seed_demo(fixture)
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    repo.record_decision(card.topic.topic_id, DecisionUpdate(
        status="needs_research", owner="Проверяющий", reason="Ручная проверка preview на синтетике", version=card.topic.version,
    ))
    saved_card = repo.get_topic(card.topic.topic_id, "replay").model_dump(mode="json")
    manifest = json.loads((REPO_ROOT / "data/demo/manifest.json").read_bytes())
    observations = []

    def check(label: str, value: dict | bytes, *, target: Repository = repo,
              counts: tuple[int, int, int] | None = None, code: str | None = None, dry_run: bool = True) -> None:
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        before = file_snapshot(folder)
        result = import_tickets(raw, dry_run=dry_run, repository=target)
        assert file_snapshot(folder) == before, f"{label}: preview changed files"
        assert repo.get_topic(card.topic.topic_id, "replay").model_dump(mode="json") == saved_card, f"{label}: decision changed"
        if code is None:
            assert result.can_commit and result.status == "valid", label
            assert (result.counts.inserted, result.counts.updated, result.counts.unchanged) == counts, label
            assert result.counts.invalid == 0, label
            print(f"{label}: valid inserted={result.counts.inserted} updated={result.counts.updated} "
                  f"unchanged={result.counts.unchanged} invalid=0")
        else:
            assert not result.can_commit and result.status == "invalid" and result.errors[0].code == code, label
            print(f"{label}: invalid {code}")
        observations.append({"case": label, "status": result.status, "counts": result.counts.model_dump(),
                             "errors": [e.code for e in result.errors]})

    print("synthetic=true; mode=preview")
    check("new-sources", manifest, counts=(4, 0, 0))
    # Только подготовка состояния для проверки повторов через уже готовый Repository.
    # Это не реализация фиксации в import_tickets().
    repo.register_dataset(DatasetRegistration.model_validate(manifest))
    check("repeat", manifest, counts=(0, 0, 4))
    delta = copy.deepcopy(manifest)
    delta["tickets"][0]["text"] += " Дополнение синтетического обращения."
    delta["tickets"][1]["source_id"] = "PREVIEW-NEW"
    check("delta", delta, counts=(1, 1, 2))
    check("duplicate-key", b'{"tickets":[],"tickets":[]}', code="JSON_DUPLICATE_KEY")
    media = copy.deepcopy(manifest)
    media["tickets"][0]["assets"] = [{"asset_id": "audio-1", "kind": "audio", "path": "call.wav", "mime_type": "audio/wav"}]
    check("media", media, code="MEDIA_NOT_SUPPORTED")
    missing = folder / "not-created" / "missing.db"
    check("missing-db", manifest, target=Repository(missing), code="PREVIEW_DATABASE_MISSING")
    assert not missing.parent.exists()
    check("write-mode", manifest, dry_run=False, code="IMPORT_NOT_IMPLEMENTED")

    wal = folder / "wal.db"
    initialize(wal)
    with sqlite3.connect(wal) as db:
        db.execute("PRAGMA journal_mode=WAL")
    db.close()
    check("wal", manifest, target=Repository(wal), code="PREVIEW_JOURNAL_UNSUPPORTED")
    incomplete = folder / "incomplete.db"
    with sqlite3.connect(incomplete) as db:
        db.execute("CREATE TABLE schema_version(version INTEGER)")
        db.execute("INSERT INTO schema_version VALUES (2)")
    db.close()
    empty = {"dataset_id": "empty", "synthetic": True, "tickets": []}
    check("incomplete-db", empty, target=Repository(incomplete), code="PREVIEW_DATABASE_UNAVAILABLE")

    restored = Repository(path).get_topic(card.topic.topic_id, "replay").model_dump(mode="json")
    assert restored == saved_card and restored["stats"]["ticket_count"] == 4
    report = folder / "result.json"
    report.write_text(json.dumps({"synthetic": True, "observations": observations, "decision_preserved": True,
                                  "history_preserved": True, "ticket_count": 4, "preview_files_unchanged": True},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: preview files unchanged; decision/history/count=4 preserved after reopen")
    print(f"report: {report.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
