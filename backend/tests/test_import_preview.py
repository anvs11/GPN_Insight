import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from app.db import initialize
from app.ingestion.importer import MAX_JSON_DEPTH, MAX_MANIFEST_BYTES, import_tickets
from app.repositories import Repository
from app.schemas import DatasetRegistration, MaterialInput, RegisteredMaterial, TicketInput
from app.storage import source_content_hash


@pytest.fixture
def manifest():
    return json.loads((REPO_ROOT / "data/demo/manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "preview.db"
    initialize(path)
    return Repository(path)


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def snapshot(folder: Path):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file()}


def preview(manifest, repo, **kwargs):
    return import_tickets(encode(manifest), dry_run=True, repository=repo, **kwargs)


def assert_invalid(result, code=None):
    assert result.status == "invalid" and result.can_commit is False
    assert result.errors and result.counts.invalid > 0
    assert (result.counts.inserted, result.counts.updated, result.counts.unchanged) == (0, 0, 0)
    if code:
        assert result.errors[0].code == code


def test_preview_counts_match_registration_without_writes(repo, manifest, monkeypatch):
    batch = DatasetRegistration.model_validate(manifest)
    repo.register_dataset(batch)
    changed = copy.deepcopy(manifest)
    changed["tickets"][0]["text"] += " Изменённое обращение."
    changed["tickets"][1]["source_id"] = "NEW-SOURCE"
    before = snapshot(repo.path.parent)

    def forbidden(*args, **kwargs):
        pytest.fail("Preview attempted a write, initialization or model call")

    import app.db as database
    import app.gateways as gateways
    monkeypatch.setattr(database, "initialize", forbidden)
    monkeypatch.setattr(Repository, "register_dataset", forbidden)
    monkeypatch.setattr(Repository, "transaction", forbidden)
    monkeypatch.setattr(gateways.UnavailableGateways, "generate", forbidden)
    result = preview(changed, repo)
    assert result.status == "valid" and result.can_commit
    assert result.counts.model_dump() == {"inserted": 1, "updated": 1, "unchanged": 2, "invalid": 0}
    assert snapshot(repo.path.parent) == before
    monkeypatch.undo()
    committed = repo.register_dataset(DatasetRegistration.model_validate(changed))
    assert committed.counts == result.counts
    assert repo.get_source(batch.tickets[0].source_id).source_version == 2


def test_empty_database_has_four_insertions_and_no_side_effects(repo, manifest):
    assert manifest["synthetic"] is True and len(manifest["tickets"]) == 4
    assert all("Синтетический пример" in t["text"] for t in manifest["tickets"])
    before = snapshot(repo.path.parent)
    for raw in (encode(manifest), encode(manifest).decode("utf-8")):
        result = import_tickets(raw, dry_run=True, repository=repo)
        assert result.counts.inserted == 4 and result.counts.invalid == 0 and result.can_commit
    assert snapshot(repo.path.parent) == before
    assert repo.list_imports() == []


def test_preview_uses_readonly_sqlite_connection(repo, manifest, monkeypatch):
    connect = sqlite3.connect
    calls = []

    def readonly(database, *args, **kwargs):
        assert str(database).endswith("?mode=ro") and kwargs.get("uri") is True
        calls.append(database)
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", readonly)
    assert preview(manifest, repo).can_commit
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["missing", "empty", "corrupt", "v1", "unknown", "missing_tables"])
def test_database_failure_does_not_create_or_migrate(tmp_path, manifest, state):
    path = tmp_path / "неизвестная база ? #.db"
    if state == "empty":
        path.touch()
    elif state == "corrupt":
        path.write_bytes(b"not a sqlite database")
    elif state in {"v1", "unknown", "missing_tables"}:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schema_version (version INTEGER)")
            db.execute("INSERT INTO schema_version VALUES (?)", (1 if state == "v1" else 999 if state == "unknown" else 2,))
    before = snapshot(tmp_path)
    result = preview(manifest, Repository(path))
    assert_invalid(result)
    if state == "missing":
        assert result.errors[0].code == "PREVIEW_DATABASE_MISSING"
    assert snapshot(tmp_path) == before


def test_database_open_error_is_not_an_empty_dataset(repo, manifest, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("secret path and source text")

    monkeypatch.setattr(sqlite3, "connect", fail)
    result = preview(manifest, repo)
    assert_invalid(result, "PREVIEW_DATABASE_UNAVAILABLE")
    assert "secret" not in result.model_dump_json()


def test_wal_database_is_rejected_without_creating_sidecar_files(repo, manifest):
    with sqlite3.connect(repo.path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db.close()
    before = snapshot(repo.path.parent)
    assert not Path(str(repo.path) + "-wal").exists()
    assert not Path(str(repo.path) + "-shm").exists()
    result = preview(manifest, repo)
    assert_invalid(result, "PREVIEW_JOURNAL_UNSUPPORTED")
    assert snapshot(repo.path.parent) == before


def test_empty_manifest_does_not_hide_missing_source_tables(tmp_path, manifest):
    path = tmp_path / "incomplete.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE schema_version (version INTEGER)")
        db.execute("INSERT INTO schema_version VALUES (2)")
    db.close()
    manifest["tickets"] = []
    before = snapshot(tmp_path)
    assert_invalid(preview(manifest, Repository(path)), "PREVIEW_DATABASE_UNAVAILABLE")
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("raw,code", [
    (b"\xff", "MANIFEST_UTF8_INVALID"),
    ("\ud800", "MANIFEST_UTF8_INVALID"),
    (b'{"dataset_id":', "JSON_INVALID"),
    (b"{} {}", "JSON_INVALID"),
    (b'{"x": NaN}', "JSON_INVALID"),
    (b'{"x": Infinity}', "JSON_INVALID"),
    (b'{"x": 1e999}', "JSON_INVALID"),
    (b'{"tickets":[], "tickets":[]}', "JSON_DUPLICATE_KEY"),
    (b'{"x":{"key":1,"key":2}}', "JSON_DUPLICATE_KEY"),
    (b'{"x":1,"\\u0078":2}', "JSON_DUPLICATE_KEY"),
    (b'{"x":"\\ud800"}', "JSON_UNICODE_INVALID"),
    (b'{"\\udfff":[]}', "JSON_UNICODE_INVALID"),
    (b"[]", "MANIFEST_FIELDS_INVALID"),
    (b" " * (MAX_MANIFEST_BYTES + 1), "MANIFEST_TOO_LARGE"),
    ("я" * (MAX_MANIFEST_BYTES // 2 + 1), "MANIFEST_TOO_LARGE"),
    (b"[" * (MAX_JSON_DEPTH + 1), "JSON_DEPTH_EXCEEDED"),
    ({"tickets": []}, "MANIFEST_TYPE_INVALID"),
])
def test_invalid_json_is_rejected_before_database_access(raw, code, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid manifest touched the database")

    monkeypatch.setattr(Repository, "preview_dataset_counts", forbidden)
    assert_invalid(import_tickets(raw, dry_run=True), code)


@pytest.mark.parametrize("field,value", [
    ("dataset_id", "../escape"), ("dataset_id", "/absolute"), ("dataset_id", ""),
    ("dataset_id", "x" * 65), ("dataset_id", 123),
    ("synthetic", "true"), ("synthetic", 1), ("synthetic", None),
    ("tickets", {}), ("tickets", None),
])
def test_invalid_manifest_fields(repo, manifest, field, value):
    manifest[field] = value
    assert_invalid(preview(manifest, repo))


@pytest.mark.parametrize("mutation", ["unknown_outer", "missing_flag", "unknown_ticket", "duplicate_source", "too_many", "foreign_product"])
def test_manifest_structure(repo, manifest, mutation):
    if mutation == "unknown_outer":
        manifest["storage_path"] = "/forbidden"
    elif mutation == "missing_flag":
        del manifest["synthetic"]
    elif mutation == "unknown_ticket":
        manifest["tickets"][0]["secret_field"] = "sensitive"
    elif mutation == "duplicate_source":
        manifest["tickets"][1]["source_id"] = manifest["tickets"][0]["source_id"]
    elif mutation == "too_many":
        manifest["tickets"] *= 26
    else:
        manifest["tickets"][0]["product_id"] = "other"
    before = snapshot(repo.path.parent)
    assert_invalid(preview(manifest, repo))
    assert snapshot(repo.path.parent) == before


@pytest.mark.parametrize("value", ["2026-10-01T09:00:00", "2026-10-01", "2026-02-30T09:00:00+03:00", "1760000000", 1760000000, None, True])
def test_created_at_requires_iso_datetime_with_timezone(repo, manifest, value):
    manifest["tickets"][0]["created_at"] = value
    assert_invalid(preview(manifest, repo), "TICKET_INVALID")


def test_valid_timezones_and_json_limit_boundaries(repo, manifest):
    manifest["tickets"][0]["created_at"] = "2026-10-01T06:00:00Z"
    # Скобки и экранированные кавычки внутри строки не увеличивают глубину JSON.
    manifest["tickets"][0]["text"] = '"\\' + "[{" * 100 + "}]" * 100
    raw = encode(manifest)
    assert import_tickets(raw + b" " * (MAX_MANIFEST_BYTES - len(raw)), dry_run=True, repository=repo).can_commit
    assert_invalid(import_tickets(raw + b" " * (MAX_MANIFEST_BYTES - len(raw) + 1), dry_run=True, repository=repo), "MANIFEST_TOO_LARGE")


def test_nested_json_at_depth_limit_reaches_schema_validation():
    raw = b"[" * MAX_JSON_DEPTH + b"0" + b"]" * MAX_JSON_DEPTH
    assert_invalid(import_tickets(raw, dry_run=True), "MANIFEST_FIELDS_INVALID")


def media(asset_id="asset-1"):
    return {"asset_id": asset_id, "kind": "audio", "path": "audio.wav", "mime_type": "audio/wav"}


@pytest.mark.parametrize("kind", ["manifest", "files", "duplicate_local", "duplicate_global", "unknown_kind"])
def test_all_media_and_duplicate_asset_ids_reject_entire_batch(repo, manifest, kind):
    assets = ()
    if kind == "files":
        assets = (b"media",)
    else:
        manifest["tickets"][0]["assets"] = [media()]
        if kind == "duplicate_local":
            manifest["tickets"][0]["assets"].append(media())
        elif kind == "duplicate_global":
            manifest["tickets"][1]["assets"] = [media()]
        elif kind == "unknown_kind":
            manifest["tickets"][0]["assets"][0]["kind"] = "video"
    before = snapshot(repo.path.parent)
    assert_invalid(preview(manifest, repo, assets=assets))
    assert snapshot(repo.path.parent) == before


def test_provenance_conflict_and_explicit_real_data_flag(repo, manifest):
    manifest["synthetic"] = False
    assert preview(manifest, repo).can_commit  # Признак не подменяется сервером.
    repo.register_dataset(DatasetRegistration.model_validate(manifest))
    manifest["synthetic"] = True
    assert_invalid(preview(manifest, repo), "SOURCE_PROVENANCE_CONFLICT")


def test_origin_conflict_is_not_unchanged(repo, manifest):
    repo.register_dataset(DatasetRegistration.model_validate(manifest).model_copy(update={"origin": "authored"}))
    assert_invalid(preview(manifest, repo), "SOURCE_PROVENANCE_CONFLICT")


def test_non_preview_mode_is_explicitly_unsupported(repo, manifest):
    before = snapshot(repo.path.parent)
    assert_invalid(import_tickets(encode(manifest), repository=repo), "IMPORT_NOT_IMPLEMENTED")
    assert snapshot(repo.path.parent) == before


def test_validation_error_does_not_echo_source_text(repo, manifest):
    secret = "PRIVATE-TEXT-SENTINEL"
    manifest["tickets"][0]["text"] = {"secret": secret}
    result = preview(manifest, repo)
    assert_invalid(result)
    assert secret not in result.model_dump_json()


def test_legacy_hashes_and_registration_survive_helper_extraction(repo):
    ticket = TicketInput(source_id="HASH-1", created_at="2026-10-01T09:00:00+03:00", text="Синтетика")
    text_hash = "b7c00e49beaf2042aebdad4fc552c3630fd8445da00df624407191428f639fa7"
    assert source_content_hash(ticket, {}) == text_hash
    registration = DatasetRegistration(dataset_id="legacy", synthetic=True, tickets=[ticket])
    repo.register_dataset(registration)
    assert repo.get_source("HASH-1").content_hash == text_hash
    assert repo.register_dataset(registration).counts.unchanged == 1

    audio = MaterialInput(**media("a1"))
    image = MaterialInput(asset_id="i1", kind="image", path="image.png", mime_type="image/png", source_url="https://internal.example/1")
    ticket = ticket.model_copy(update={"assets": [image, audio]})
    prepared = [RegisteredMaterial(asset_id=a.asset_id, kind=a.kind, mime_type=a.mime_type, original_filename=a.path,
                                   storage_path="server/" + a.path, content_hash=("a" if a.kind == "audio" else "b") * 64, byte_size=123)
                for a in ticket.assets]
    material_map = {m.asset_id: m for m in prepared}
    media_hash = "098043301a817ac9725d75d513d0f44b14638a8c569c189cd76e2b048dc0b738"
    assert source_content_hash(ticket, material_map) == media_hash
    registration = DatasetRegistration(dataset_id="legacy", synthetic=True, tickets=[ticket], materials=prepared)
    assert repo.register_dataset(registration).counts.updated == 1
    assert repo.get_source("HASH-1").content_hash == media_hash
    for item in prepared:
        item.storage_path = "another/" + item.original_filename
    assert repo.register_dataset(registration).counts.unchanged == 1
    assert repo.get_source("HASH-1").source_version == 2
