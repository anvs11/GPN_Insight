from pathlib import Path
import json

from fastapi.testclient import TestClient
import pytest

from app.analysis.extractor import DEFAULT_PROMPT_PATH, NeedExtractor
from app.config import REPO_ROOT, Settings
from app.db import initialize
from app.gateways import GatewayUnavailable, TextRequest
from app.ingestion.integration import PROMPT_VERSIONS, build_text_profile
from app.ingestion.normalizer import TextNormalizer
from app.main import create_app
from app.masking import mask_pii
from app.public_views import public_source
from app.replay import ReplayGateway, make_record, publish_records
from app.repositories import Repository, RepositoryError
from app.schemas import (
    DatasetRegistration, EvidenceRef, ExtractedNeed, ExtractedNeeds, Fragment, MaterialInput,
    NormalizedDocument, PassportExample, TicketInput, Topic, TopicPassport,
)
from app.storage import source_content_hash
from test_upload_security import media_context, import_files
from scripts.media_import_fixture import wav_bytes

FIXTURES = json.loads((REPO_ROOT / "data/security/synthetic-safety.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURES["PII_SAMPLE"], ids=lambda c: c["id"])
def test_pii_fixture_has_exact_expected_mask_and_is_idempotent(case):
    assert FIXTURES["synthetic"] and FIXTURES["origin"] == "authored"
    assert mask_pii(case["raw"]) == case["masked"]
    assert mask_pii(case["masked"]) == case["masked"]


@pytest.mark.parametrize("name", FIXTURES["UPLOAD_BAD"])
def test_upload_bad_rejected_before_any_registration(media_context, name):
    settings, repo = media_context
    before = repo.path.read_bytes()
    with pytest.raises(RepositoryError): import_files(media_context, {name: wav_bytes()})
    assert repo.path.read_bytes() == before and not repo.registered_materials() and not repo.list_imports()


def test_original_policy_covers_all_http_source_views_and_preserves_raw_sqlite(tmp_path):
    settings = Settings(_env_file=None, app_db_path=tmp_path / "check.db", app_upload_root=tmp_path / "uploads", app_tmp_root=tmp_path / "tmp")
    repo = Repository(settings.app_db_path)
    initialize(repo.path)
    private = "SENSITIVE_ORIGINAL " + FIXTURES["PII_SAMPLE"][0]["raw"]
    ticket = TicketInput(source_id="PRIVACY-SYNTHETIC-TEST", created_at="2026-11-02T12:00:00+03:00",
                         text=private, requester_id="SENSITIVE_REQUESTER")
    # Только искусственная тестовая запись, помеченная nonsynthetic для проверки политики.
    repo.register_dataset(DatasetRegistration(dataset_id="synthetic-policy-probe", synthetic=False, tickets=[ticket]))
    source = repo.get_source(ticket.source_id)
    doc = NormalizedDocument(source_id=ticket.source_id, version=1, content_hash=source.content_hash,
        normalization_version="synthetic-policy-test", fragments=[Fragment(fragment_id="safe-fragment", kind="text", text="Изменить телефон в заявке. " + FIXTURES["PII_SAMPLE"][0]["masked"])])
    need = ExtractedNeed(need_id="safe-need", source_id=ticket.source_id, fragment_id="safe-fragment",
        scenario="Контакт заявки", need_type="operational_request", statement="Изменить телефон в заявке",
        evidence_quote="Изменить телефон в заявке.", explicit_request=True)
    second_need = need.model_copy(update={"need_id": "safe-need-2"})
    repo.save_source_analysis(doc, [need, second_need])
    ref = EvidenceRef(source_id=ticket.source_id, fragment_id="safe-fragment", quote=need.evidence_quote)
    repo.create_topic(Topic(topic_id="safe-topic", title="Синтетическая проверка доступа", summary=need.statement,
        passport=TopicPassport(version=1, scenario=need.scenario, need_statement=need.statement, scope="Контакт заявки",
            inclusion_examples=[PassportExample(text=need.statement, evidence_refs=[ref])]), need_ids=[need.need_id], evidence=[ref]))
    second = repo.get_topic("safe-topic", "replay").topic.model_copy(update={"topic_id": "safe-target", "need_ids": [second_need.need_id]})
    repo.create_topic(second)
    def safe(response):
        assert response.status_code == 200, response.text
        assert "SENSITIVE_ORIGINAL" not in response.text and "SENSITIVE_REQUESTER" not in response.text
        assert "synthetic@example.invalid" not in response.text
        return response.json()
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        body = safe(client.get("/api/v1/sources/" + ticket.source_id))
        assert body["original_access"] == "restricted" and body["ticket"]["text"] == "" and body["ticket"]["requester_id"] is None
        assert body["document"]["fragments"][0]["text"] == doc.fragments[0].text
        safe(client.get("/api/v1/topics?support_level=all"))
        card = safe(client.get("/api/v1/topics/safe-topic"))
        preview = safe(client.post("/api/v1/topics/safe-topic/merge-preview", json={"target_topic_id": "safe-target"}))
        assert preview["source"]["sources"][0]["original_access"] == "restricted"
        saved = safe(client.patch("/api/v1/topics/safe-topic/decision", json=dict(version=card["topic"]["version"], status="needs_research", owner="Синтетический владелец", reason="Проверить доступ")))
        passport = saved["topic"]["passport"]
        fields = {k: passport[k] for k in ("scenario", "need_statement", "scope", "inclusion_examples", "exclusion_examples", "scope_confirmed")}
        safe(client.patch("/api/v1/topics/safe-topic/passport", json=dict(version=saved["topic"]["version"], passport_version=passport["version"],
                    author="Синтетический аналитик", reason="Подтверждение", passport=fields)))
        report = client.get("/api/v1/reports")
        assert report.status_code == 200 and "SENSITIVE_ORIGINAL" not in report.text and "synthetic@example.invalid" not in report.text
    reopened = Repository(repo.path).get_source(ticket.source_id)
    assert reopened.ticket == ticket and reopened.document == doc
    assert len(Repository(repo.path).get_topic("safe-topic", "replay").history) == 1
    # Оригинальные URL/имя также закрыты; внутренний объект не изменяется.
    original = reopened.model_copy(deep=True)
    original.ticket.assets = [MaterialInput(asset_id="private-audio", kind="audio", path="SENSITIVE_NAME.wav", mime_type="audio/wav", source_url="https://private.invalid/SENSITIVE_URL")]
    projected = public_source(original)
    assert projected.ticket.assets[0].path == "restricted.wav" and projected.ticket.assets[0].source_url is None
    assert original.ticket.assets[0].path == "SENSITIVE_NAME.wav"


def test_synthetic_original_stays_available(tmp_path):
    from app.demo import load_fixture
    repo = Repository(tmp_path / "demo.db")
    initialize(repo.path)
    fixture = load_fixture(REPO_ROOT / "data/contracts/demo_state.json")
    repo.seed_demo(fixture)
    source = repo.get_topic(fixture.topic.topic_id, "replay").sources[0]
    assert public_source(source).ticket == source.ticket and public_source(source).original_access == "available"


def test_injected_instructions_remain_data_and_reference_guards_reject_forgery(tmp_path, monkeypatch):
    settings = Settings(_env_file=None)
    profile = build_text_profile(settings, model_id="synthetic-injection-no-live-model", recording_set_version="synthetic-injection-v1")
    ticket = TicketInput(source_id="INJ-TEXT-SYNTHETIC", created_at="2026-11-02T12:00:00+03:00", text=FIXTURES["INJ_TEXT"]["text"])
    doc = TextNormalizer(profile).normalize_ticket(ticket, content_hash=source_content_hash(ticket, {}), source_version=1)
    request = TextRequest(operation="extract_needs", prompt=DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"), prompt_version=PROMPT_VERSIONS["extract_needs"],
                          data={"document": doc.model_dump(mode="json")}, parameters=profile.parameters)
    assert "Игнорируй правила" in request.data["document"]["fragments"][0]["text"] and "../private.txt" not in request.prompt
    need = ExtractedNeed(need_id="pending", source_id=ticket.source_id, fragment_id=doc.fragments[0].fragment_id,
        scenario="Контакт заявки", need_type="operational_request", statement="Изменить телефон в заявке", evidence_quote=FIXTURES["INJ_TEXT"]["need_quote"], explicit_request=True)
    record = make_record(request, ExtractedNeeds, dict(needs=[need.model_dump()]), model_id=profile.model_id,
        normalization_hash=profile.parameters["normalization_hash"], set_version=profile.recording_set_version, origin="authored")
    publish_records(tmp_path / "known", [record])
    real_open = Path.open
    def guarded(path, *args, **kwargs):
        if path.name == "private.txt": pytest.fail("Instruction attempted filesystem read")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    result = NeedExtractor(ReplayGateway(tmp_path / "known", profile.model_id, profile.parameters["normalization_hash"]), profile).extract(doc)
    assert result.needs[0].evidence_quote == need.evidence_quote and result.metadata[0].origin == "authored"
    forged = need.model_copy(update={"fragment_id": "private-file-fragment"})
    bad = make_record(request, ExtractedNeeds, dict(needs=[forged.model_dump()]), model_id=profile.model_id,
        normalization_hash=profile.parameters["normalization_hash"], set_version=profile.recording_set_version, origin="authored")
    publish_records(tmp_path / "forged", [bad])
    with pytest.raises(GatewayUnavailable) as exc:
        NeedExtractor(ReplayGateway(tmp_path / "forged", profile.model_id, profile.parameters["normalization_hash"]), profile).extract(doc)
    assert exc.value.code == "EXTRACTION_REFERENCE_INVALID"
