import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import schemas
from app.cli import validate_http_examples
from app.config import REPO_ROOT
from app.db import connection, initialize
from app.main import create_app
from app.repositories import Repository


def prepare(settings, fixture):
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repo = Repository(path)
    repo.seed_demo(fixture)
    return repo


def test_ordinary_start_has_no_synthetic_cards(settings, fixture):
    for _ in range(2):
        with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
            response = client.get("/api/v1/topics")
            assert response.status_code == 200 and response.json() == []
            assert client.get(f"/api/v1/topics/{fixture.topic.topic_id}").status_code == 404
            assert client.get(f"/api/v1/sources/{fixture.sources[0].source_id}").status_code == 404
    with connection(settings.repo_path(settings.app_db_path)) as db:
        assert db.execute("SELECT count(*) FROM demo_fixtures").fetchone()[0] == 0


def test_both_minimal_decisions_and_history_survive_restart(settings, fixture):
    repo = prepare(settings, fixture)
    path = f"/api/v1/topics/{fixture.topic.topic_id}"
    history = []
    for status in ("needs_research", "new"):
        with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
            before = client.get(path).json()
            body = {"status": status, "owner": "Тестовый владелец", "reason": "Проверить источники", "version": before["topic"]["version"]}
            response = client.patch(path + "/decision", json=body)
            assert response.status_code == 200
            saved = response.json()
            assert saved["topic"]["status"] == status
            assert saved["topic"]["owner"] == body["owner"]
            assert saved["topic"]["version"] == before["topic"]["version"] + 1
            assert saved["history"][:-1] == history
            event = saved["history"][-1]
            assert (event["previous_status"], event["new_status"]) == (before["topic"]["status"], status)
            assert event["owner"] == body["owner"] and event["reason"] == body["reason"]
            assert event["topic_version"] == saved["topic"]["version"]
            history = saved["history"]
            conflict = client.patch(path + "/decision", json=body)
            assert conflict.status_code == 409 and conflict.json()["code"] == "VERSION_CONFLICT"
            assert client.get(path).json() == saved
        initialize(repo.path)
        assert repo.seed_demo(fixture) is False
        with TestClient(create_app(settings), base_url="http://127.0.0.1") as restarted:
            assert restarted.get(path).json() == saved
            assert saved["stats"]["ticket_count"] == 4
            assert saved["synthetic"] is True and saved["analysis_mode"] is None


@pytest.mark.parametrize("status", ["new", "needs_research"])
def test_minimal_decisions_require_nonblank_owner_reason_and_integer_version(settings, fixture, status):
    prepare(settings, fixture)
    path = f"/api/v1/topics/{fixture.topic.topic_id}"
    valid = {"status": status, "owner": "Тестовый владелец", "reason": "Проверить источники", "version": 1}
    invalid = [{k: v for k, v in valid.items() if k != field} for field in ("owner", "reason", "version")]
    invalid += [{**valid, field: value} for field in ("owner", "reason") for value in ("", " \t\n", None)]
    invalid += [{**valid, "version": value} for value in (True, "1", 1.0, 0, None)]
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        before = client.get(path).json()
        for body in invalid:
            response = client.patch(path + "/decision", json=body)
            assert response.status_code == 422
            assert response.json()["code"] == "VALIDATION_ERROR"
        assert client.get(path).json() == before


def test_concurrent_http_decisions_keep_one_history_event(settings, fixture):
    prepare(settings, fixture)
    path = f"/api/v1/topics/{fixture.topic.topic_id}"
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        def save(status):
            return client.patch(path + "/decision", json={"status": status, "owner": status, "reason": "Проверка конкуренции", "version": 1})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(save, ("new", "needs_research")))
        assert sorted(r.status_code for r in responses) == [200, 409]
        saved = next(r.json() for r in responses if r.status_code == 200)
        assert client.get(path).json() == saved
        assert len(saved["history"]) == 1 and saved["topic"]["version"] == 2


def test_registered_source_read_is_not_a_fixture_response(settings, fixture):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        ticket = fixture.sources[0].model_copy(update={"source_id": "API-UNPROCESSED"})
        client.app.state.repository.register_dataset(schemas.DatasetRegistration(
            dataset_id="api-synthetic", product_id="webneft", synthetic=True, origin="authored", tickets=[ticket],
        ))
        response = client.get("/api/v1/sources/API-UNPROCESSED")
        assert response.status_code == 200
        source = schemas.SourceDetail.model_validate(response.json())
        assert source.ticket == ticket
        assert source.document is None and source.needs == [] and source.requires_analysis
        assert client.get("/api/v1/topics").json() == []


def test_documented_http_examples_match_real_reads(settings, fixture):
    prepare(settings, fixture)
    examples_path = REPO_ROOT / "data/contracts/http-examples.json"
    assert validate_http_examples(examples_path) == 7
    responses = json.loads(examples_path.read_text(encoding="utf-8"))["responses"]
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        for name in ("topic", "source"):
            example = responses[name]
            response = client.get(example["path"])
            assert response.status_code == example["status_code"]
            assert response.json() == example["body"]
        topic_path = responses["topic"]["path"]
        for filename in ("decision.json", "decision-new.json"):
            body = json.loads((examples_path.parent / filename).read_text(encoding="utf-8"))
            body["version"] = client.get(topic_path).json()["topic"]["version"]
            assert client.patch(topic_path + "/decision", json=body).status_code == 200
            assert client.patch(topic_path + "/decision", json=body).json() == responses["version_conflict"]["body"]
        assert client.patch(topic_path + "/decision", json={}).json() == responses["validation_error"]["body"]
