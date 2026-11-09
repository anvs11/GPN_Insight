from datetime import datetime
import json

from fastapi.testclient import TestClient

from app.bootstrap import configured_text_pipeline
from app.clock import fixed_clock
from app.db import connection
from app.gateways import text_gateway
from app.main import create_app
from app.repositories import Repository
from scripts.prepare_acceptance_demo import prepare_acceptance_demo
from scripts.partial_pipeline_fixture import run_sync


def test_varied_demo_replays_partial_retry_without_losing_decision_or_source_counts(tmp_path):
    demo, _ = prepare_acceptance_demo(tmp_path)
    cards = demo.repo.list_topics("replay", support_level="all")
    assert len(cards) == 3 and sorted(c.stats.ticket_count for c in cards) == [1, 1, 2]
    contact = next(c for c in cards if "изменения контакта" in c.topic.title)
    assert contact.topic.status == "needs_research" and len(contact.history) == 1
    partial = demo.repo.processing_result(demo.ticket.source_id)
    assert partial.state == "partial"
    with fixed_clock(datetime.fromisoformat("2026-11-09T12:00:00+03:00")):
        with TestClient(create_app(demo.settings), base_url="http://127.0.0.1") as client:
            assert client.get("/api/v1/health").json()["pipeline_ready"]
            assert len(client.get("/api/v1/topics").json()) == 1  # Реальная повторяемость по двум source_id.
            reviews = client.get("/api/v1/reviews").json()
            assert len(reviews) == 1 and reviews[0]["evidence_refs"][0]["source_id"] == "ACCEPT-UNCLEAR-1"
            manifest = json.loads((tmp_path / "text-manifest.json").read_text(encoding="utf-8"))
            repeated = client.post("/api/v1/imports", files={"manifest": (None, json.dumps(manifest))})
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["counts"]["unchanged"] == 4
            weekly = client.get("/api/v1/reports/weekly")
            assert weekly.status_code == 200 and "2026-11-02 — 2026-11-08" in weekly.text
            assert all(code in weekly.text for code in ["suspected_defect", "improvement_request", "unclassified", "unknown"])
            assert "завершённая неделя=5" in weekly.text
    adapters, error = configured_text_pipeline(demo.settings, text_gateway(demo.settings))
    assert adapters and error is None
    full = run_sync(demo.repo, adapters, "demo-retry")
    assert full.state == "completed" and full.material_coverage.successful == 9 and full.source_coverage.complete == 5
    restored = Repository(demo.repo.path).get_topic(contact.topic.topic_id, "replay")
    assert restored.history == contact.history and restored.topic.passport == contact.topic.passport
    assert restored.topic.status == contact.topic.status and restored.stats.ticket_count == 1
    repeat = run_sync(demo.repo, adapters, "demo-repeat")
    assert repeat.state == "completed" and repeat.completed_steps == 0 and repeat.reused_steps > 0
    assert demo.repo.get_topic(contact.topic.topic_id, "replay") == restored
    assert demo.repo.processing_result(demo.ticket.source_id, revision_id=partial.revision_id) == partial
    with connection(demo.repo.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
