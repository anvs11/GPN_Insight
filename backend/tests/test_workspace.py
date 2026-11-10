from datetime import datetime
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from app.clock import fixed_clock
from app.db import connection, initialize
from app.main import create_app
from app.repositories import Repository, RepositoryError
from app.schemas import DatasetRegistration, TicketInput
from scripts.prepare_acceptance_demo import prepare_acceptance_demo
from scripts.partial_pipeline_fixture import run_sync
from app.bootstrap import configured_text_pipeline
from app.gateways import text_gateway


@pytest.fixture
def workspace(tmp_path):
    demo, _ = prepare_acceptance_demo(tmp_path)
    with TestClient(create_app(demo.settings), base_url="http://127.0.0.1") as client:
        yield client, demo


def test_monitoring_counts_include_unassigned_and_pending_sources_and_use_real_assessments(workspace):
    client, demo = workspace
    demo.repo.register_dataset(DatasetRegistration(dataset_id="pending", synthetic=True, tickets=[TicketInput(
        source_id="PENDING-1", created_at="2026-11-08T23:59:00+03:00", text="Ещё не обработанное синтетическое обращение")]))
    topics = client.get("/api/v1/monitoring").json()
    assert topics["total"] == 3 and sorted(c["ticket_count"] for c in topics["items"]) == [1, 1, 2]
    sources = client.get("/api/v1/monitoring?view=sources").json()
    assert sources["total"] == 6 and sources["items"][0]["id"] == "PENDING-1"
    pending = sources["items"][0]
    assert pending["status"] == "pending" and pending["unassessed"] and pending["priorities"] == []
    unclear = next(i for i in sources["items"] if i["id"] == "ACCEPT-UNCLEAR-1")
    assert unclear["categories"] == ["unclassified"] and unclear["priorities"] == ["unknown"] and unclear["topic_ids"] == []
    partial = next(i for i in sources["items"] if i["id"] == demo.ticket.source_id)
    assert partial["status"] == "partial" and partial["errors"] == 1
    assert client.get("/api/v1/monitoring?view=topics&support=repeated").json()["total"] == 1
    assert client.get("/api/v1/monitoring?view=sources&priority=elevated").json()["total"] == 2
    assert client.get("/api/v1/monitoring?view=sources&date_from=2026-11-08&date_to=2026-11-08").json()["total"] == 1
    first = client.get("/api/v1/monitoring?view=sources&limit=2").json()
    second = client.get("/api/v1/monitoring?view=sources&limit=2&offset=2").json()
    assert first["total"] == second["total"] == 6 and not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


@pytest.mark.parametrize("query", ["view=bad", "status=bad", "view=sources&status=linked", "date_from=2026-11-08&date_to=2026-11-01", "limit=101", "offset=-1", "q=a&q=b", "unexpected=x"])
def test_monitoring_rejects_unknown_or_conflicting_filters(workspace, query):
    client, _ = workspace
    assert client.get("/api/v1/monitoring?" + query).status_code == 422


def test_snapshot_selection_preserves_decisions_is_idempotent_escapes_text_and_survives_reopen(workspace):
    client, demo = workspace
    card = client.get("/api/v1/monitoring?view=topics&support=repeated").json()["items"][0]
    source_id = card["source_ids"][0]
    source = client.get("/api/v1/monitoring/source/" + source_id).json()
    before = demo.repo.get_topic(card["id"], "replay")
    body = dict(title="Синтетическая подборка <script>alert(1)</script>", summary="Комментарий автора", comment="Не меняет решений",
        selections=[dict(kind="topic", id=card["id"], version=card["version"], revision=card["revision"]), dict(kind="source", id=source_id, version=source["version"], revision=source["revision"])])
    saved = client.post("/api/v1/reports/archive", json=body)
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert saved["selection_count"] == 2 and saved["distinct_source_count"] == 2
    assert "<script>" not in saved["markdown"] and saved["cards"][0]["priorities"] == ["elevated"]
    assert client.post("/api/v1/reports/archive", json=body).json() == saved
    assert client.get("/api/v1/reports/archive").json()["total"] == 1
    changed = client.patch(f"/api/v1/topics/{card['id']}/decision", json=dict(version=card["version"], status="needs_research", owner="Синтетический владелец", reason="Проверка архива"))
    assert changed.status_code == 200
    assert client.post("/api/v1/reports/archive", json=body).status_code == 409
    assert client.get("/api/v1/reports/archive/" + saved["report_id"]).json() == saved
    assert before.history == [] and len(changed.json()["history"]) == 1
    with connection(demo.repo.path) as db:
        restored = json.loads(db.execute("SELECT body_json FROM report_snapshots").fetchone()[0])
        assert restored == saved and db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with TestClient(create_app(demo.settings), base_url="http://127.0.0.1") as reopened:
        assert reopened.get("/api/v1/reports/archive/" + saved["report_id"]).json() == saved


def test_weekly_snapshot_repeat_and_transaction_rollback(workspace, monkeypatch):
    client, demo = workspace
    with fixed_clock(datetime.fromisoformat("2026-11-10T12:00:00+03:00")):
        first = client.post("/api/v1/reports/archive/weekly").json()
        assert first["period"]["period"]["date_from"] == "2026-11-02"
        assert client.post("/api/v1/reports/archive/weekly").json() == first
    from app import report_archive
    original = report_archive.save_snapshot
    def fail(db, payload):
        original(db, payload)
        raise RepositoryError("SYNTHETIC_ROLLBACK", "Синтетическая ошибка записи", 503)
    monkeypatch.setattr(report_archive, "save_snapshot", fail)
    item = client.get("/api/v1/monitoring").json()["items"][0]
    body = dict(title="Rollback", selections=[{k: item[k] for k in ("kind", "id", "version", "revision")}])
    assert client.post("/api/v1/reports/archive", json=body).status_code == 503
    assert client.get("/api/v1/reports/archive").json()["total"] == 1


def test_v4_to_v5_migration_backup_rollback_and_data_preservation(workspace, monkeypatch):
    _, demo = workspace
    before = demo.repo.get_source(demo.ticket.source_id)
    with connection(demo.repo.path) as db:
        db.execute("DROP TABLE report_snapshots")
        db.execute("UPDATE schema_version SET version=4")
    from app import db as database
    original = database.migrate_v4
    def fail(db):
        original(db)
        raise RuntimeError("Synthetic migration failure")
    monkeypatch.setattr(database, "migrate_v4", fail)
    with pytest.raises(RuntimeError): initialize(demo.repo.path)
    with connection(demo.repo.path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 4
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_snapshots'").fetchone()
    monkeypatch.setattr(database, "migrate_v4", original)
    result = initialize(demo.repo.path)
    assert result.version == 5 and result.migrated_from == 4 and result.backup_path.exists()
    assert Repository(demo.repo.path).get_source(demo.ticket.source_id) == before
    assert initialize(demo.repo.path).backup_path is None


def test_monitoring_and_snapshot_do_not_expose_restricted_original(workspace):
    client, demo = workspace
    demo.repo.register_dataset(DatasetRegistration(dataset_id="private-synthetic-probe", synthetic=False, tickets=[TicketInput(
        source_id="PRIVATE-PROBE", created_at="2026-11-08T12:00:00+03:00", text="SENSITIVE_SENTINEL", requester_id="SENSITIVE_REQUESTER")]))
    result = client.get("/api/v1/monitoring?view=sources&q=PRIVATE-PROBE")
    assert result.status_code == 200 and "SENSITIVE_SENTINEL" not in result.text and "SENSITIVE_REQUESTER" not in result.text
    item = result.json()["items"][0]
    saved = client.post("/api/v1/reports/archive", json=dict(title="Synthetic privacy probe", selections=[{k: item[k] for k in ("kind", "id", "version", "revision")}]))
    assert saved.status_code == 200 and "SENSITIVE_SENTINEL" not in saved.text


def test_same_source_version_does_not_hide_changed_analysis_in_report_selection(workspace):
    client, demo = workspace
    old = client.get("/api/v1/monitoring/source/" + demo.ticket.source_id).json()
    body = dict(title="Synthetic partial snapshot", selections=[{k: old[k] for k in ("kind", "id", "version", "revision")}])
    first = client.post("/api/v1/reports/archive", json=body).json()
    adapters, error = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    assert adapters and error is None
    assert run_sync(demo.repo, adapters, "report-full-retry").state == "completed"
    current = client.get("/api/v1/monitoring/source/" + demo.ticket.source_id).json()
    assert current["version"] == old["version"] == 1 and current["revision"] != old["revision"]
    assert client.post("/api/v1/reports/archive", json=body).status_code == 409
    assert client.get("/api/v1/reports/archive/" + first["report_id"]).json() == first
    body["selections"] = [{k: current[k] for k in ("kind", "id", "version", "revision")}]
    assert client.post("/api/v1/reports/archive", json=body).status_code == 200


def test_concurrent_identical_snapshots_have_one_immutable_row(workspace):
    client, demo = workspace
    card = client.get("/api/v1/monitoring").json()["items"][0]
    body = dict(title="Synthetic parallel report", selections=[{k: card[k] for k in ("kind", "id", "version", "revision")}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v1/reports/archive", json=body), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    assert client.get("/api/v1/reports/archive").json()["total"] == 1


def test_corrupted_archive_is_visible_and_not_overwritten(workspace):
    client, demo = workspace
    card = client.get("/api/v1/monitoring").json()["items"][0]
    body = dict(title="Synthetic integrity probe", selections=[{k: card[k] for k in ("kind", "id", "version", "revision")}])
    saved = client.post("/api/v1/reports/archive", json=body).json()
    with connection(demo.repo.path) as db:
        changed = {**saved, "title": "SENSITIVE_CORRUPTION_SENTINEL"}
        raw = json.dumps(changed)
        db.execute("UPDATE report_snapshots SET body_json=? WHERE report_id=?", (raw, saved["report_id"]))
    for response in [client.get("/api/v1/reports/archive"), client.get("/api/v1/reports/archive/" + saved["report_id"]), client.post("/api/v1/reports/archive", json=body)]:
        assert response.status_code == 503 and response.json()["code"] == "REPORT_INTEGRITY_FAILED"
        assert "SENSITIVE_CORRUPTION_SENTINEL" not in response.text
    with connection(demo.repo.path) as db:
        assert db.execute("SELECT body_json FROM report_snapshots").fetchone()[0] == raw
