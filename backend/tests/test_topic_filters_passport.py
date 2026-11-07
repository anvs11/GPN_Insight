from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json

import pytest
from fastapi.testclient import TestClient

from app.db import connection, initialize
from app.main import create_app
from app.repositories import Repository
from app.schemas import DecisionUpdate
from app.config import REPO_ROOT
from test_human_workflow import state


def prepare(settings, fixture):
    initialize(settings.app_db_path)
    repo = Repository(settings.app_db_path)
    repo.seed_demo(fixture)
    return repo


def command(card):
    return dict(version=card.topic.version, passport_version=card.topic.passport.version,
        author="Синтетический аналитик", reason="Проверены цитаты и границы",
        title="Уточнённое название той же темы",
        passport={**card.topic.passport.model_dump(exclude={"product_id", "version"}, mode="json"),
                  "scope_confirmed": True})


def test_period_uses_moscow_dates_distinct_sources_and_separate_all_time(settings, fixture):
    dates = ["2026-10-31T21:00:00Z", "2026-11-01T20:59:59Z", "2026-11-01T21:00:00Z", "2026-10-31T20:59:59Z"]
    sources = [s.model_copy(update={"created_at": datetime.fromisoformat(d),
        "requester_id": "synthetic-same-requester" if i < 2 else None}) for i, (s, d) in enumerate(zip(fixture.sources, dates))]
    extra = fixture.needs[0].model_copy(update={"need_id": "synthetic-second-need-same-source"})
    fixture = fixture.model_copy(update={"sources": sources, "needs": [*fixture.needs, extra],
        "topic": fixture.topic.model_copy(update={"need_ids": [*fixture.topic.need_ids, extra.need_id]})})
    repo = prepare(settings, fixture)
    before = state(repo)
    path = "/api/v1/topics/" + fixture.topic.topic_id
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        query = "date_from=2026-11-01&date_to=2026-11-01"
        cards = client.get("/api/v1/topics?" + query).json()
        assert len(cards) == 1
        card = cards[0]
        assert card["stats"]["ticket_count"] == 4 and card["stats"]["requester_count"] is None
        assert card["period_stats"]["ticket_count"] == 2 and card["period_stats"]["requester_count"] == 1
        assert card["period_stats"]["date_from"] == "2026-11-01"
        assert len(card["topic"]["need_ids"]) == 5
        assert client.get(path + "?" + query).json() == card
        assert client.get("/api/v1/topics?date_from=2027-01-01").json() == []
        assert client.get(path + "?date_from=2027-01-01").json()["period_stats"]["ticket_count"] == 0
        assert client.get(path).json()["period_stats"] is None
    assert state(repo) == before


def test_filters_combine_status_support_and_review_due(settings, fixture):
    repo = prepare(settings, fixture)
    repo.record_decision(fixture.topic.topic_id, DecisionUpdate(version=1, status="deferred", owner="Тест",
        reason="Пересмотреть", review_after="2000-01-01"))
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert len(client.get("/api/v1/topics").json()) == 1
        assert len(client.get("/api/v1/topics?product_id=webneft&status=deferred&review_due=true&support_level=all").json()) == 1
        for query in ("status=new", "review_due=false", "support_level=single"):
            assert client.get("/api/v1/topics?" + query).json() == []


@pytest.mark.parametrize("query", ["status=unknown", "product_id=other", "review_due=yes", "date_from=bad",
    "date_from=2026-12-01&date_to=2026-11-01", "status=new&status=new", "support_level=single&support_level=repeated",
    "date_from=2026-11-01&date_from=2026-11-01", "ignored=1"])
def test_invalid_filters_are_explicit_and_read_only(settings, fixture, query):
    repo = prepare(settings, fixture)
    before = state(repo)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/topics?" + query).status_code == 422
    assert state(repo) == before


def test_passport_preserves_decision_links_and_history_after_reopen(settings, fixture):
    repo = prepare(settings, fixture)
    repo.record_decision(fixture.topic.topic_id, DecisionUpdate(version=1, status="needs_research", owner="Владелец", reason="Исследовать"))
    before = repo.get_topic(fixture.topic.topic_id, "replay")
    revision = repo.catalog_snapshot().catalog_revision
    body = command(before)
    path = "/api/v1/topics/" + fixture.topic.topic_id
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        saved = client.patch(path + "/passport", json=body)
        assert saved.status_code == 200
        value = saved.json()
        assert value["topic"]["topic_id"] == before.topic.topic_id
        assert value["topic"]["version"] == 3 and value["topic"]["passport"]["version"] == 2
        assert value["topic"]["passport"]["scope_confirmed"] is True
        assert value["topic"]["status"] == "needs_research" and value["topic"]["owner"] == "Владелец"
        assert value["history"] == [e.model_dump(mode="json") for e in before.history]
        assert value["stats"] == before.stats.model_dump(mode="json")
        assert value["topic"]["need_ids"] == before.topic.need_ids
        assert client.patch(path + "/passport", json=body).status_code == 409
        changes = client.get(path + "/changes").json()
        assert len(changes) == 1 and changes[0]["kind"] == "passport"
        assert changes[0]["author"] == body["author"] and changes[0]["reason"] == body["reason"]
    assert repo.catalog_snapshot().catalog_revision == revision + 1
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get(path).json() == value


@pytest.mark.parametrize("invalid", ["author", "reason", "version", "passport_version", "product", "evidence", "confirmed", "examples", "query"])
def test_passport_rejects_invalid_input_without_changes(settings, fixture, invalid):
    repo = prepare(settings, fixture)
    body = command(repo.get_topic(fixture.topic.topic_id, "replay"))
    expected = 422
    if invalid in {"author", "reason"}:
        body[invalid] = "   "
    elif invalid == "version":
        body["version"] = True
    elif invalid == "passport_version":
        body["passport_version"] = 2
        expected = 409
    elif invalid == "product":
        body["passport"]["product_id"] = "other"
    elif invalid == "evidence":
        body["passport"]["inclusion_examples"][0]["evidence_refs"][0]["quote"] = "Выдуманная цитата"
    elif invalid == "confirmed":
        body["passport"]["scope_confirmed"] = "true"
    elif invalid == "examples":
        body["passport"]["inclusion_examples"] = []
    before = state(repo)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/passport" + ("?dry_run=true" if invalid == "query" else ""), json=body)
        assert response.status_code == expected
    assert state(repo) == before


def test_passport_history_failure_rolls_back_everything(settings, fixture):
    repo = prepare(settings, fixture)
    body = command(repo.get_topic(fixture.topic.topic_id, "replay"))
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER fail_passport_history BEFORE INSERT ON topic_changes BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    before = state(repo)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/passport", json=body).status_code == 503
    assert state(repo) == before


def test_concurrent_passport_edits_only_one_is_saved(settings, fixture):
    repo = prepare(settings, fixture)
    body = command(repo.get_topic(fixture.topic.topic_id, "replay"))
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda title: client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/passport",
                json={**body, "title": title}).status_code, ["Название A", "Название B"]))
    assert sorted(statuses) == [200, 409]
    assert len(repo.topic_changes(fixture.topic.topic_id)) == 1


def test_merged_passport_is_read_only_and_explicit_status_filter_finds_it(settings, fixture):
    from app.workflow import HumanWorkflow
    from app.schemas import MergeTopicsRequest
    from test_human_workflow import exclude, resolve_command
    repo = prepare(settings, fixture)
    removed, _ = exclude(repo, fixture)
    review = HumanWorkflow(repo).resolve_review(removed.review_ids[0], resolve_command(repo, fixture, "create"))
    target = repo.get_topic(review.resolution.topic_id, "replay")
    source = repo.get_topic(fixture.topic.topic_id, "replay")
    HumanWorkflow(repo).merge_topics(source.topic.topic_id, MergeTopicsRequest(version=source.topic.version,
        target_topic_id=target.topic.topic_id, target_version=target.topic.version, author="Тест", reason="Проверить merged"), "replay")
    before = state(repo)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        merged = repo.get_topic(fixture.topic.topic_id, "replay")
        assert client.patch(f"/api/v1/topics/{merged.topic.topic_id}/passport", json=command(merged)).status_code == 409
        found = client.get("/api/v1/topics?status=merged&support_level=all").json()
        assert [c["topic"]["topic_id"] for c in found] == [fixture.topic.topic_id]
        assert len(client.get("/api/v1/topics?&support_level=all").json()) == 1
    assert state(repo) == before


def test_handoff_json_examples_match_actual_http(settings, fixture):
    prepare(settings, fixture)
    examples = json.loads((REPO_ROOT / "data/contracts/topic-filter-examples.json").read_text(encoding="utf-8"))
    workflow = json.loads((REPO_ROOT / "data/contracts/workflow-examples.json").read_text(encoding="utf-8"))
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        for example in examples["examples"]:
            response = client.get("/api/v1/topics?" + example["query"])
            assert response.status_code == 200
            assert [c["topic"]["topic_id"] for c in response.json()] == example["expected_topic_ids"]
            if "period_stats" in example:
                assert response.json()[0]["period_stats"] == example["period_stats"]
        body = next(e["value"] for e in workflow["examples"] if e["schema"] == "PassportUpdate")
        saved = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/passport", json=body)
        assert saved.status_code == 200 and saved.json()["topic"]["title"] == body["title"]


def test_manual_passport_content_is_not_labelled_as_model_output(settings, fixture):
    repo = prepare(settings, fixture)
    with connection(repo.path) as db:
        db.execute("UPDATE topics SET origin='model',analysis_mode='replay' WHERE topic_id=?", (fixture.topic.topic_id,))
    before = repo.get_topic(fixture.topic.topic_id, "replay")
    assert before.data_origin == "model"
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        saved = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/passport", json=command(before))
        assert saved.status_code == 200 and saved.json()["data_origin"] == "authored"
        assert saved.json()["analysis_mode"] == "replay" and saved.json()["synthetic"] is True
