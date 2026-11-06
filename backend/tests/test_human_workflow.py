from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.db import connection, initialize
from app.main import create_app
from app.repositories import Repository, RepositoryError
from app.schemas import (
    DatasetRegistration, DecisionUpdate, EvidenceRef, ExcludeNeedsRequest, MergeTopicsRequest,
    PassportExample, ProposedTopic, ResolveReviewRequest, Topic,
)
from app.workflow import HumanWorkflow


@pytest.fixture
def repo(settings, fixture):
    path = settings.repo_path(settings.app_db_path)
    initialize(path)
    repository = Repository(path)
    repository.seed_demo(fixture)
    return repository


def exclude(repo, fixture, ids=None):
    card = repo.get_topic(fixture.topic.topic_id, "replay")
    command = ExcludeNeedsRequest(version=card.topic.version, need_ids=ids or [fixture.needs[0].need_id],
        author="Аналитик", reason="Проверить неверную связь")
    result = HumanWorkflow(repo).exclude_needs(card.topic.topic_id, command)
    return result, command


def proposal(need):
    ref = EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)
    return ProposedTopic(title="Явно ручная синтетическая тема", summary=need.statement, scenario=need.scenario,
        need_statement=need.statement, scope="Границы для проверки аналитиком",
        inclusion_examples=[PassportExample(text=need.statement, evidence_refs=[ref])])


def resolve_command(repo, fixture, action):
    return ResolveReviewRequest(version=1, action=action, author="Аналитик", reason="Ручная проверка источника",
        topic_id=fixture.topic.topic_id if action == "attach" else None,
        topic_versions={fixture.topic.topic_id: repo.get_topic(fixture.topic.topic_id, "replay").topic.version} if action == "attach" else {},
        proposed_topic=proposal(fixture.needs[0]) if action == "create" else None)


def state(repo):
    with connection(repo.path) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: sorted([tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')], key=repr) for table in tables}


@pytest.mark.parametrize("action", ["attach", "create", "insufficient_data"])
def test_review_resolution_is_atomic_idempotent_and_persistent(repo, fixture, action):
    excluded, _ = exclude(repo, fixture)
    review_id = excluded.review_ids[0]
    assert repo.get_topic(fixture.topic.topic_id, "replay").stats.ticket_count == 3
    command = resolve_command(repo, fixture, action)
    service = HumanWorkflow(repo)
    saved = service.resolve_review(review_id, command)
    assert saved.version == 2 and saved.resolution.author == command.author and saved.resolution.request_hash
    assert saved.status == ("insufficient_data" if action == "insufficient_data" else "resolved")
    if action == "attach":
        assert repo.get_topic(fixture.topic.topic_id, "replay").stats.ticket_count == 4
    elif action == "create":
        new = repo.get_topic(saved.resolution.topic_id, "replay")
        assert new.topic.status == "new" and new.topic.owner is None and new.data_origin == "authored"
        assert new.stats.ticket_count == 1 and new.synthetic and not new.topic.passport.scope_confirmed
    else:
        with pytest.raises(RepositoryError) as exc:
            card = repo.get_topic(fixture.topic.topic_id, "replay")
            repo.attach_needs(card.topic.topic_id, [fixture.needs[0].need_id], expected_version=card.topic.version)
        assert exc.value.code == "MANUAL_OVERRIDE"
    before = state(repo)
    assert service.resolve_review(review_id, command) == saved
    assert HumanWorkflow(Repository(repo.path)).resolve_review(review_id, command) == saved
    assert state(repo) == before and len(repo.review_history(review_id)) == 2
    with pytest.raises(RepositoryError) as exc:
        service.resolve_review(review_id, command.model_copy(update={"reason": "Другое решение"}))
    assert exc.value.code == "VERSION_CONFLICT"


def test_review_resolution_failure_rolls_back_links_overrides_and_history(repo, fixture, monkeypatch):
    excluded, _ = exclude(repo, fixture)
    before = state(repo)
    original = Repository.save_review
    def fail(self, review, **kwargs):
        if review.status != "open":
            raise RuntimeError("injected")
        return original(self, review, **kwargs)
    monkeypatch.setattr(Repository, "save_review", fail)
    with pytest.raises(RuntimeError):
        HumanWorkflow(repo).resolve_review(excluded.review_ids[0], resolve_command(repo, fixture, "create"))
    assert state(repo) == before


@pytest.mark.parametrize("same", [False, True])
def test_concurrent_review_resolutions_are_serialized(repo, fixture, same):
    excluded, _ = exclude(repo, fixture)
    command = resolve_command(repo, fixture, "attach")
    other = command if same else command.model_copy(update={"reason": "Другое подтверждение"})
    def apply(body):
        try:
            return HumanWorkflow(Repository(repo.path)).resolve_review(excluded.review_ids[0], body).status
        except RepositoryError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [command, other]))
    assert sorted(results) == (["resolved", "resolved"] if same else ["VERSION_CONFLICT", "resolved"])
    assert len(repo.review_history(excluded.review_ids[0])) == 2


def test_old_source_review_cannot_be_resolved_and_old_success_is_not_reapplied(repo, fixture):
    excluded, _ = exclude(repo, fixture)
    review_id = excluded.review_ids[0]
    command = resolve_command(repo, fixture, "attach")
    saved = HumanWorkflow(repo).resolve_review(review_id, command)
    repo.register_dataset(DatasetRegistration(dataset_id="changed", synthetic=True, origin="authored",
        tickets=[fixture.sources[0].model_copy(update={"text": "Изменившийся синтетический источник"})]))
    before = state(repo)
    repeated = HumanWorkflow(repo).resolve_review(review_id, command)
    assert repeated.requires_recheck and repeated.resolution == saved.resolution and state(repo) == before
    assert repo.get_source(fixture.sources[0].source_id).requires_analysis
    with pytest.raises(RepositoryError) as exc:
        HumanWorkflow(repo).resolve_review(review_id, command.model_copy(update={"version": 2, "reason": "Новое"}))
    assert exc.value.code == "VERSION_CONFLICT"


def test_open_stale_review_returns_409(repo, fixture):
    excluded, _ = exclude(repo, fixture)
    command = resolve_command(repo, fixture, "create")
    repo.register_dataset(DatasetRegistration(dataset_id="changed", synthetic=True, origin="authored",
        tickets=[fixture.sources[0].model_copy(update={"text": "Новый текст"})]))
    before = state(repo)
    with pytest.raises(RepositoryError) as exc:
        HumanWorkflow(repo).resolve_review(excluded.review_ids[0], command)
    assert exc.value.code == "VERSION_CONFLICT" and state(repo) == before


def test_exclude_is_atomic_and_repeat_does_not_duplicate_queue(repo, fixture, monkeypatch):
    before = state(repo)
    original = Repository.save_review
    monkeypatch.setattr(Repository, "save_review", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(RuntimeError):
        exclude(repo, fixture)
    assert state(repo) == before
    monkeypatch.setattr(Repository, "save_review", original)
    result, command = exclude(repo, fixture)
    saved = state(repo)
    assert HumanWorkflow(repo).exclude_needs(fixture.topic.topic_id, command) == result and state(repo) == saved
    assert len(repo.list_reviews()) == 1
    with pytest.raises(RepositoryError) as exc:
        repo.attach_needs(fixture.topic.topic_id, command.need_ids, expected_version=result.topic_version)
    assert exc.value.code == "MANUAL_OVERRIDE"


def test_invalid_exclusion_and_changed_topic_versions_do_not_write(repo, fixture):
    before = state(repo)
    for command in (
        ExcludeNeedsRequest(version=100, need_ids=[fixture.needs[0].need_id], author="А", reason="Р"),
        ExcludeNeedsRequest(version=1, need_ids=["missing"], author="А", reason="Р"),
    ):
        with pytest.raises(RepositoryError):
            HumanWorkflow(repo).exclude_needs(fixture.topic.topic_id, command)
        assert state(repo) == before


def two_topics(tmp_path, fixture):
    path = tmp_path / "merge.db"
    initialize(path)
    repo = Repository(path)
    extra = fixture.needs[0].model_copy(update={"need_id": "same-source-different-need"})
    repo.register_dataset(DatasetRegistration(dataset_id="merge-synthetic", synthetic=True, tickets=fixture.sources))
    for document in fixture.documents:
        source = repo.get_source(document.source_id)
        needs = [need for need in [*fixture.needs, extra] if need.source_id == document.source_id]
        repo.save_source_analysis(document.model_copy(update={"content_hash": source.content_hash}), needs)
    repo.create_topic(fixture.topic)
    target = Topic.model_validate({**fixture.topic.model_dump(), "topic_id": "merge-target", "need_ids": [extra.need_id],
        "evidence": [EvidenceRef(source_id=extra.source_id, fragment_id=extra.fragment_id, quote=extra.evidence_quote)]})
    repo.create_topic(target)
    return repo, fixture.topic.topic_id, target.topic_id


def merge_command(repo, source, target, **extra):
    return MergeTopicsRequest(version=repo.get_topic(source, "replay").topic.version, target_topic_id=target,
        target_version=repo.get_topic(target, "replay").topic.version, author="Аналитик", reason="Одна потребность", **extra)


def test_merge_keeps_target_decision_distinct_counts_history_and_receipt(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    repo.record_decision(target, DecisionUpdate(version=1, status="needs_research", owner="Владелец", reason="Сохранить"))
    before = repo.get_topic(target, "replay")
    service = HumanWorkflow(repo)
    preview = service.merge_preview(source, target, "replay")
    assert not preview.decision_conflict and preview.target.history == before.history
    command = merge_command(repo, source, target)
    revision = repo.catalog_snapshot().catalog_revision
    receipt = service.merge_topics(source, command, "replay")
    saved = repo.get_topic(target, "replay")
    assert saved.topic.status == "needs_research" and saved.topic.owner == "Владелец" and saved.history == before.history
    assert saved.stats.ticket_count == 4 and len(saved.topic.need_ids) == 5
    assert repo.catalog_snapshot().catalog_revision > revision
    old = repo.get_topic(source, "replay")
    assert old.topic.status == "merged" and old.topic.canonical_topic_id == target and old.history[-1].new_status == "merged"
    assert set(repo.list_topic_ids(None)) == {target}
    snapshot = state(repo)
    assert HumanWorkflow(Repository(repo.path)).merge_topics(source, command, "replay") == receipt
    assert state(repo) == snapshot
    assert repo.topic_changes(source)[-1].kind == repo.topic_changes(target)[-1].kind == "merge"


def test_merge_conflict_requires_confirmation_and_both_versions(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    repo.record_decision(source, DecisionUpdate(version=1, status="declined", owner="Первый", reason="Не нужно"))
    repo.record_decision(target, DecisionUpdate(version=1, status="needs_research", owner="Второй", reason="Проверить"))
    service = HumanWorkflow(repo)
    command = merge_command(repo, source, target)
    assert service.merge_preview(source, target, "replay").decision_conflict
    before = state(repo)
    with pytest.raises(RepositoryError) as exc:
        service.merge_topics(source, command, "replay")
    assert exc.value.code == "MERGE_DECISION_CONFLICT" and state(repo) == before
    stale = command.model_copy(update={"confirm_decision_conflict": True, "target_version": 1})
    with pytest.raises(RepositoryError) as exc:
        service.merge_topics(source, stale, "replay")
    assert exc.value.code == "VERSION_CONFLICT" and state(repo) == before
    service.merge_topics(source, command.model_copy(update={"confirm_decision_conflict": True}), "replay")
    assert repo.get_topic(target, "replay").topic.owner == "Второй"


def test_merge_failure_restores_both_topics_and_overrides(tmp_path, fixture, monkeypatch):
    repo, source, target = two_topics(tmp_path, fixture)
    service = HumanWorkflow(repo)
    command = merge_command(repo, source, target)
    before = state(repo)
    monkeypatch.setattr(HumanWorkflow, "_event", lambda *args: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(RuntimeError):
        service.merge_topics(source, command, "replay")
    assert state(repo) == before


def test_merge_chain_resolves_old_id_and_rejects_cycle(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    # Для третьей темы берём одну явно исключённую потребность и создаём вручную.
    result = HumanWorkflow(repo).exclude_needs(source, ExcludeNeedsRequest(version=1,
        need_ids=[fixture.needs[1].need_id], author="А", reason="Отдельно"))
    need = fixture.needs[1]
    third = HumanWorkflow(repo).resolve_review(result.review_ids[0], ResolveReviewRequest(version=1, action="create",
        proposed_topic=proposal(need), author="А", reason="Ручная тема")).resolution.topic_id
    service = HumanWorkflow(repo)
    service.merge_topics(source, merge_command(repo, source, target), "replay")
    service.merge_topics(target, merge_command(repo, target, third), "replay")
    assert Repository(repo.path).get_topic(source, "replay").topic.canonical_topic_id == third
    for a, b in ((third, third), (third, source)):
        with pytest.raises(RepositoryError):
            service.merge_topics(a, merge_command(repo, a, b), "replay")


def test_review_http_aliases_validation_and_restart(settings, repo, fixture):
    excluded, _ = exclude(repo, fixture)
    review_id = excluded.review_ids[0]
    command = resolve_command(repo, fixture, "attach").model_dump(mode="json")
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/review-items").json() == client.get("/api/v1/reviews").json()
        for invalid in ({**command, "version": True}, {**command, "author": " "}, {**command, "action": "merge"}):
            assert client.post(f"/api/v1/review-items/{review_id}/resolve", json=invalid).status_code == 422
        saved = client.post(f"/api/v1/review-items/{review_id}/resolve", json=command)
        assert saved.status_code == 200
        assert client.post(f"/api/v1/reviews/{review_id}/resolve", json=command).json() == saved.json()
        changed = client.post(f"/api/v1/reviews/{review_id}/resolve", json={**command, "reason": "Другое"})
        assert changed.status_code == 409
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get(f"/api/v1/review-items/{review_id}").json() == saved.json()
        assert client.get(f"/api/v1/topics/{fixture.topic.topic_id}/changes").status_code == 200


@pytest.mark.parametrize("status,fields", [
    ("linked", {"existing_issue_ref": "WEB-123"}), ("deferred", {"review_after": "2026-11-01"}),
    ("declined", {}), ("implemented", {"implemented_at": "2026-10-20T09:00:00Z", "implementation_comment": "Исправлено"}),
])
def test_full_http_decisions_and_old_versions(settings, repo, fixture, status, fields):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        url = f"/api/v1/topics/{fixture.topic.topic_id}/decision"
        command = {"version": 1, "status": status, "owner": "Владелец", "reason": "Решение", **fields}
        saved = client.patch(url, json=command)
        assert saved.status_code == 200 and saved.json()["topic"]["status"] == status
        assert client.patch(url, json=command).status_code == 409
        assert len(saved.json()["history"]) == 1
        assert saved.json()["stats"]["review_due"] == (status == "deferred")
    assert Repository(repo.path).get_topic(fixture.topic.topic_id, "replay").topic.status == status


@pytest.mark.parametrize("ref", ["http://tracker.internal/WEB-1", "https://evil.example/WEB-1", "https://user:secret@tracker.internal/WEB-1", "javascript:alert(1)"])
def test_unapproved_issue_urls_do_not_save_decision(settings, repo, fixture, ref):
    settings.app_issue_hosts = "tracker.internal"
    before = state(repo)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        result = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/decision", json={"version": 1, "status": "linked",
            "owner": "Владелец", "reason": "Решение", "existing_issue_ref": ref})
        assert result.status_code == 422 and result.json()["code"] == "ISSUE_REF_INVALID"
    assert state(repo) == before


def test_approved_internal_issue_url_saves_without_network(settings, repo, fixture):
    settings.app_issue_hosts = "tracker.internal"
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        result = client.patch(f"/api/v1/topics/{fixture.topic.topic_id}/decision", json={"version": 1, "status": "linked",
            "owner": "Владелец", "reason": "Решение", "existing_issue_ref": "https://tracker.internal/WEB-1"})
        assert result.status_code == 200


def test_source_decision_is_not_silently_lost_into_undecided_target(tmp_path, fixture):
    repo, source, target = two_topics(tmp_path, fixture)
    repo.record_decision(source, DecisionUpdate(version=1, status="declined", owner="Владелец", reason="Причина отказа"))
    service = HumanWorkflow(repo)
    assert service.merge_preview(source, target, "replay").decision_conflict
    before = state(repo)
    with pytest.raises(RepositoryError) as exc:
        service.merge_topics(source, merge_command(repo, source, target), "replay")
    assert exc.value.code == "MERGE_DECISION_CONFLICT" and state(repo) == before


def test_multiple_needs_are_resolved_together_and_foreign_evidence_is_rejected(repo, fixture):
    from app.schemas import ReviewItem
    exclude(repo, fixture, [need.need_id for need in fixture.needs[:2]])
    refs = [EvidenceRef(source_id=n.source_id, fragment_id=n.fragment_id, quote=n.evidence_quote) for n in fixture.needs[:2]]
    grouped = ReviewItem(review_id="grouped", version=1, kind="assignment", need_ids=[n.need_id for n in fixture.needs[:2]],
        reason="Ручная групповая проверка", evidence_refs=refs)
    repo.save_review(grouped)
    command = ResolveReviewRequest(version=1, action="create", author="А", reason="Одна тема", proposed_topic=proposal(fixture.needs[0]))
    saved = HumanWorkflow(repo).resolve_review(grouped.review_id, command)
    assert repo.get_topic(saved.resolution.topic_id, "replay").stats.ticket_count == 2
    bad = grouped.model_copy(update={"review_id": "foreign", "need_ids": [fixture.needs[2].need_id]})
    repo.save_review(bad)
    before = state(repo)
    with pytest.raises(RepositoryError) as exc:
        HumanWorkflow(repo).resolve_review("foreign", ResolveReviewRequest(version=1, action="insufficient_data", author="А", reason="Р"))
    assert exc.value.code == "INVALID_EVIDENCE" and state(repo) == before


def test_mutation_query_cannot_silently_turn_dry_run_into_write(settings, repo, fixture):
    before = state(repo)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        result = client.post(f"/api/v1/topics/{fixture.topic.topic_id}/exclude?dry_run=true", json={"version": 1,
            "need_ids": [fixture.needs[0].need_id], "author": "А", "reason": "Р"})
        assert result.status_code == 422
    assert state(repo) == before


def test_pipeline_repeat_keeps_manual_resolution_with_new_profile(settings, fixture):
    from pipeline_adapters import TestComponents
    from test_pipeline import setup, wait_run
    repo = setup(settings, fixture, 1)
    first = TestComponents(decision="review")
    with TestClient(create_app(settings, pipeline_adapters=first.adapters()), base_url="http://127.0.0.1") as client:
        assert wait_run(repo, client.post("/api/v1/runs", json={}).json()["run_id"]).state == "completed"
    review = repo.list_reviews()[0]
    need = repo.get_source(fixture.sources[0].source_id).needs[0]
    resolved = HumanWorkflow(repo).resolve_review(review.review_id, ResolveReviewRequest(version=1, action="create",
        author="Аналитик", reason="Ручное создание", proposed_topic=proposal(need)))
    before = repo.get_topic(resolved.resolution.topic_id, "replay")
    second = TestComponents()  # Выдал бы другое new, если бы сервер вызвал assign.
    second.profile.recording_set_version = "test-2"
    with TestClient(create_app(settings, pipeline_adapters=second.adapters()), base_url="http://127.0.0.1") as client:
        run = wait_run(repo, client.post("/api/v1/runs", json={}).json()["run_id"])
        assert run.state == "completed" and all(stage != "assign" for stage, source in second.events)
    assert Repository(repo.path).get_topic(before.topic.topic_id, "replay") == before
