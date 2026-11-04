import json
import re
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app import schemas
from app.cli import validate_examples
from app.config import REPO_ROOT, Settings
from app.db import connection, initialize
from app.demo import DemoFixture, load_fixture
from app.gateways import ApprovedAsset, GatewayUnavailable, TextRequest, UnavailableGateways
from app.main import create_app
from app.repositories import Repository, RepositoryError


@pytest.fixture
def fixture():
    return load_fixture(REPO_ROOT / "data/contracts/demo_state.json")


@pytest.fixture
def tmp_path():
    # Системный Temp Windows может иметь ACL от другого запуска pytest.
    # Уникальные каталоги проекта не требуют изменения прав или очистки чужих данных.
    path = REPO_ROOT / "var" / "pytest" / uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, app_db_path=tmp_path / "insight.db")


def seed(settings, fixture):
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repo = Repository(path)
    assert repo.seed_demo(fixture)
    return repo


def test_contract_examples_and_invalid_assignment():
    assert validate_examples(REPO_ROOT / "data/contracts/examples.json") == 16
    bundle = json.loads((REPO_ROOT / "data/contracts/examples.json").read_text(encoding="utf-8"))
    assignment = next(x["value"] for x in bundle["examples"] if x["schema"] == "TopicAssignment")
    with pytest.raises(ValidationError):
        schemas.TopicAssignment.model_validate({**assignment, "candidate_set_complete": False, "search_truncated": True})
    batch = next(x["value"] for x in bundle["examples"] if x["schema"] == "BatchAssignment")
    for injection in ({"decision": "new"}, {"candidate_set_complete": True}):
        with pytest.raises(ValidationError):
            schemas.BatchAssignment.model_validate({**batch, **injection})


def test_schema_rejects_fabricated_time_and_unclosed_review(fixture):
    raw = "  Синтетический текст с пробелами.\n"
    ticket = schemas.TicketInput(source_id="raw", created_at="2026-10-01T12:00:00+03:00", text=raw)
    assert ticket.text == raw
    preview = schemas.ImportPreview(status="invalid", counts=schemas.ImportCounts(invalid=1), errors=[schemas.ImportIssue(code="INVALID_JSON", message="Не удалось разобрать manifest")], can_commit=False)
    assert preview.dataset_id is None and preview.errors[0].source_id is None
    with pytest.raises(ValidationError):
        schemas.TicketInput(source_id="x", created_at="2026-10-01T12:00:00", text="Синтетика")
    with pytest.raises(ValidationError):
        schemas.Fragment(fragment_id="f", asset_id="a", kind="asr", text="Синтетика", start_ms=100)
    with pytest.raises(ValidationError):
        schemas.ReviewItem(review_id="r", version=1, kind="assignment", need_ids=[fixture.needs[0].need_id], reason="Тест", evidence_refs=[], status="resolved")


def test_health_never_assumes_model_success(settings, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Health не должен обращаться в сеть")
    monkeypatch.setattr(socket, "create_connection", no_network)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        health = response.json()
        assert health["model_mode"] == "replay"
        assert health["processing_state"] == "not_implemented"
        assert health["replay_record_count"] == 0 and health["replay_origins"] == []
        assert all(m["checked_at"] is None and m["code"] == "REPLAY_NOT_IMPLEMENTED" for m in health["available_models"].values())
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 200
    live = settings.model_copy(update={"app_model_mode": "live", "text_model_base_url": "http://internal.invalid", "text_model_id": "not-probed"})
    with TestClient(create_app(live), base_url="http://127.0.0.1") as client:
        health = client.get("/api/v1/health").json()
        assert health["model_mode"] == "live"
        assert health["available_models"]["text"]["code"] == "MODEL_ADAPTER_NOT_IMPLEMENTED"
        assert health["available_models"]["asr"]["code"] == "MODEL_NOT_CONFIGURED"


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_gateways_fail_explicitly(settings, mode):
    gateways = UnavailableGateways(settings.model_copy(update={"app_model_mode": mode}))
    asset = ApprovedAsset("synthetic", Path("nonexistent.wav"), "a" * 64, "audio/wav")
    request = TextRequest(operation="extract_needs", prompt="Пример контракта", prompt_version="example", data={})
    for call in (lambda: gateways.generate(request, schemas.ExtractedNeed), lambda: gateways.transcribe(asset), lambda: gateways.describe(asset)):
        with pytest.raises(GatewayUnavailable) as error:
            call()
        assert error.value.code == ("MODEL_NOT_CONFIGURED" if mode == "live" else "REPLAY_NOT_IMPLEMENTED")


def test_card_decision_survives_restart_and_exact_seed(settings, fixture):
    repo = seed(settings, fixture)
    path = f"/api/v1/topics/{fixture.topic.topic_id}"
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        card = client.get(path).json()
        assert card["stats"]["ticket_count"] == 4
        assert card["stats"]["requester_count"] is None
        assert card["analysis_mode"] is None and card["data_origin"] == "authored"
        assert sum(card["stats"]["weekly_counts"].values()) == 4
        assert len(client.get("/api/v1/topics").json()) == 1
        source = client.get(f"/api/v1/sources/{fixture.sources[0].source_id}").json()
        assert source["document"]["fragments"][0]["text"] == fixture.sources[0].text
        body = {"status": "needs_research", "owner": "Тестовый владелец", "reason": "Проверены источники", "version": 1}
        saved = client.patch(path + "/decision", json=body)
        assert saved.status_code == 200
        assert saved.json()["topic"]["version"] == 2
        assert client.patch(path + "/decision", json=body).status_code == 409
    initialize(repo.path)
    assert repo.seed_demo(fixture) is False
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as restarted:
        card = restarted.get(path).json()
        assert card["topic"]["status"] == "needs_research"
        assert card["topic"]["owner"] == "Тестовый владелец"
        assert len(card["history"]) == 1 and card["history"][0]["reason"] == "Проверены источники"
        assert card["stats"]["ticket_count"] == 4


def test_decision_errors_do_not_change_card(settings, fixture):
    repo = seed(settings, fixture)
    path = f"/api/v1/topics/{fixture.topic.topic_id}/decision"
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.patch(path, json={"status": "needs_research", "version": 1}).status_code == 422
        assert client.patch(path, json={"status": "needs_research", "version": 1, "owner": "Тест", "reason": "   "}).status_code == 422
        assert client.patch(path, json={"status": "deferred", "version": 1, "owner": "Тест", "reason": "Нет даты"}).status_code == 422
        response = client.patch(path, json={"status": "declined", "version": 1, "owner": "Тест", "reason": "Ещё не реализовано"})
        assert response.status_code == 501 and response.json()["code"] == "FEATURE_NOT_IMPLEMENTED"
        assert client.get("/api/v1/topics/missing").status_code == 404
        assert client.post("/api/v1/runs", json={}).status_code == 404
    assert repo.get_topic(fixture.topic.topic_id, "replay").topic.version == 1


def test_concurrent_decisions_use_version(settings, fixture):
    repo = seed(settings, fixture)
    def save(owner):
        try:
            repo.record_decision(fixture.topic.topic_id, schemas.DecisionUpdate(status="needs_research", version=1, owner=owner, reason="Тест гонки сохранения"))
            return "saved"
        except RepositoryError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["Владелец А", "Владелец Б"]))
    assert sorted(results) == ["VERSION_CONFLICT", "saved"]
    assert len(repo.get_topic(fixture.topic.topic_id, "replay").history) == 1


def test_counts_use_unique_sources_and_unknown_requesters(settings, fixture):
    data = fixture.model_dump(mode="json")
    extra = {**data["needs"][0], "need_id": "second-need-same-source"}
    data["needs"].append(extra)
    data["topic"]["need_ids"].append(extra["need_id"])
    # Частичные сведения о заявителях не выдаём за полное число клиентов.
    data["sources"][0]["requester_id"] = "synthetic-requester"
    repo = seed(settings, DemoFixture.model_validate(data))
    stats = repo.get_topic(fixture.topic.topic_id, "replay").stats
    assert stats.ticket_count == 4 and stats.requester_count is None


def test_invalid_evidence_and_changed_fixture_preserve_state(settings, fixture):
    repo = seed(settings, fixture)
    data = fixture.model_dump(mode="json")
    data["needs"][0]["evidence_quote"] = "Выдуманная цитата, которой нет в источнике"
    with pytest.raises(ValidationError):
        DemoFixture.model_validate(data)
    data = fixture.model_dump(mode="json")
    data["topic"]["title"] = "Изменённое начальное состояние"
    with pytest.raises(RepositoryError, match="Начальное состояние изменилось"):
        repo.seed_demo(DemoFixture.model_validate(data))
    assert repo.get_topic(fixture.topic.topic_id, "replay").topic.title == fixture.topic.title


def test_validation_and_origin_do_not_echo_data(settings, fixture):
    seed(settings, fixture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        path = f"/api/v1/topics/{fixture.topic.topic_id}/decision"
        response = client.patch(path, json={"private_input": "DO_NOT_ECHO"})
        assert response.status_code == 422 and "DO_NOT_ECHO" not in response.text
        assert client.patch(path, headers={"Origin": "https://external.invalid"}, json={}).status_code == 403
        assert client.get("/api/v1/health", headers={"Host": "external.invalid"}).status_code == 400


def test_paths_are_repo_relative(settings, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert settings.repo_path(Path("var/insight.db")) == REPO_ROOT / "var/insight.db"
    assert settings.text_model_token.get_secret_value() == ""


@pytest.mark.parametrize("mutation", ["invented_text", "fake_media"])
def test_demo_fragments_must_be_grounded_in_raw_source(fixture, mutation):
    data = fixture.model_dump(mode="json")
    fragment = data["documents"][0]["fragments"][0]
    if mutation == "invented_text":
        fragment["text"] = "Выдуманное содержимое, отсутствующее в исходном обращении."
        data["needs"][0]["evidence_quote"] = fragment["text"]
        data["topic"]["evidence"][0]["quote"] = fragment["text"]
        data["topic"]["passport"]["inclusion_examples"][0]["evidence_refs"][0]["quote"] = fragment["text"]
    else:
        fragment.update(kind="asr", asset_id="unregistered-audio")
    with pytest.raises(ValidationError):
        DemoFixture.model_validate(data)


@pytest.mark.parametrize("version", [True, "1", 1.0])
def test_version_requires_json_integer(settings, fixture, version):
    repo = seed(settings, fixture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/decision", json={
            "status": "needs_research", "owner": "Тест", "reason": "Проверка типа версии", "version": version,
        })
        assert response.status_code == 422
    assert repo.get_topic(fixture.topic.topic_id, "replay").topic.version == 1


def test_unsupported_filters_do_not_return_unfiltered_success(settings, fixture):
    seed(settings, fixture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/topics?status=declined")
        assert response.status_code == 422
        assert response.json()["code"] == "UNSUPPORTED_FILTER"


def test_failed_history_insert_rolls_back_decision(settings, fixture):
    repo = seed(settings, fixture)
    with connection(repo.path) as db:
        db.execute("CREATE TRIGGER reject_decision BEFORE INSERT ON decision_events BEGIN SELECT RAISE(ABORT, 'TEST_INSERT_FAILURE'); END")
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/decision", json={
            "status": "needs_research", "owner": "Тест", "reason": "DO_NOT_ECHO", "version": 1,
        })
        assert response.status_code == 503
        assert "TEST_INSERT_FAILURE" not in response.text and "DO_NOT_ECHO" not in response.text
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    assert card.topic.version == 1 and card.topic.status == "new" and card.topic.owner is None
    assert card.history == [] and card.stats.ticket_count == 4


def test_failed_seed_is_atomic(settings, fixture):
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    with connection(path) as db:
        db.execute("CREATE TRIGGER reject_seed BEFORE INSERT ON needs BEGIN SELECT RAISE(ABORT, 'TEST_SEED_FAILURE'); END")
    with pytest.raises(sqlite3.IntegrityError):
        Repository(path).seed_demo(fixture)
    with connection(path) as db:
        for table in ("sources", "needs", "topics", "topic_needs", "demo_fixtures"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_health_and_settings_do_not_expose_tokens(settings, mode):
    token = "SYNTHETIC-SECRET-SENTINEL"
    configured = settings.model_copy(update={
        "app_model_mode": mode,
        "text_model_token": SecretStr(token), "asr_token": SecretStr(token), "vl_token": SecretStr(token),
        "text_model_id": "not-probed", "text_model_base_url": "http://internal.invalid",
    })
    assert token not in repr(configured) and token not in configured.model_dump_json()
    with TestClient(create_app(configured), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200 and token not in response.text
        assert all(m["status"] != "available" for m in response.json()["available_models"].values())


def test_russian_examples_survive_files_http_and_sqlite(settings, fixture):
    def check_strings(value):
        if isinstance(value, str):
            assert "??" not in value, "В синтетическом примере потеряна кодировка текста"
        elif isinstance(value, dict):
            for item in value.values():
                check_strings(item)
        elif isinstance(value, list):
            for item in value:
                check_strings(item)

    for name in ("demo_state.json", "examples.json", "decision.json"):
        check_strings(json.loads((REPO_ROOT / "data/contracts" / name).read_text(encoding="utf-8")))
    assert fixture.topic.title == "Контактный телефон в оформленной заявке"
    assert all(re.search(r"[А-Яа-яЁё]", source.text) for source in fixture.sources)
    update = schemas.DecisionUpdate.model_validate_json((REPO_ROOT / "data/contracts/decision.json").read_text(encoding="utf-8"))
    assert update.owner == "Тестовый владелец"
    repo = seed(settings, fixture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/decision", json=update.model_dump(mode="json"))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json; charset=utf-8"
        assert response.json()["history"][0]["owner"] == update.owner
        assert response.json()["history"][0]["reason"] == update.reason
    saved = repo.get_topic(fixture.topic.topic_id, "replay")
    assert saved.topic.owner == "Тестовый владелец"
    assert saved.history[0].reason == update.reason
