import pytest

from app.config import Settings
from app.analysis.extractor import NeedExtractor
from app.gateways import GatewayResult, GatewayUnavailable
from app.ingestion.integration import build_text_pipeline, build_text_profile
from app.replay import ReplayGateway, make_record
from app.repositories import RepositoryError
from app.schemas import (
    BatchAssignment, CatalogSnapshot, EvidenceRef, ExtractedNeed, ExtractedNeeds, MaterialError,
    PassportExample, TicketInput, Topic, TopicPassport,
)


@pytest.fixture
def settings():
    return Settings(_env_file=None)


@pytest.fixture
def profile(settings):
    return build_text_profile(settings, model_id="synthetic-integration-no-live-model",
        recording_set_version="text-integration-authored-v1", parameters={"temperature": 0, "flag": False})


@pytest.fixture
def document(settings, profile):
    ticket = TicketInput(source_id="SYNTHETIC-BOUNDARY-001", created_at="2026-11-06T12:00:00+03:00",
        text="Синтетика. Связаться: demo@example.invalid, +7 (000) 123-45-67.")
    return build_text_pipeline(None, profile, settings).normalizer.normalize_ticket(ticket, content_hash="a" * 64, source_version=1)


def payload(document):
    return ExtractedNeeds(needs=[ExtractedNeed(need_id="pending", source_id=document.source_id,
        fragment_id=document.fragments[0].fragment_id, evidence_quote="Связаться: [EMAIL], [ТЕЛЕФОН].",
        scenario="Синтетическая связь", need_type="operational_request", statement="Связаться с заявителем", explicit_request=True)])


class Gateway:
    def __init__(self, profile, values, mutate=None):
        self.profile, self.values, self.mutate = profile, list(values), mutate
        self.requests = []

    def generate(self, request, response_schema):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        record = make_record(request, response_schema, value.model_dump(mode="json"), model_id=self.profile.model_id,
            normalization_hash=self.profile.parameters["normalization_hash"], set_version=self.profile.recording_set_version, origin="authored")
        result = GatewayResult(value=value, metadata=record.metadata)
        return self.mutate(result) if self.mutate else result


def test_current_analysis_receives_all_parameters_and_real_metadata(settings, profile, document):
    gateway = Gateway(profile, [payload(document), ExtractedNeeds(needs=[])])
    adapters = build_text_pipeline(gateway, profile, settings)
    result = adapters.analysis.extract_needs(document)
    assert len(result.needs) == 1 and result.needs[0].need_id == "pending"
    assert result.needs[0].evidence_quote in document.fragments[0].text
    assert gateway.requests[0].parameters == profile.parameters
    assert gateway.requests[0].prompt_version == "extract-needs-v1"
    assert result.metadata[0].origin == "authored"
    assert adapters.analysis.extract_needs(document).needs == []


@pytest.mark.parametrize("field", ["source_id", "fragment_id", "evidence_quote"])
def test_forged_extraction_never_crosses_pipeline_boundary(settings, profile, document, field):
    value = payload(document)
    setattr(value.needs[0], field, "synthetic-forged")
    gateway = Gateway(profile, [value])
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == ("EXTRACTION_QUOTE_INVALID" if field == "evidence_quote" else "EXTRACTION_REFERENCE_INVALID")
    assert len(gateway.requests) == 1


def test_material_failure_is_rejected_before_gateway(settings, profile, document):
    document.errors = [MaterialError(source_id=document.source_id, stage="normalization", code="SYNTHETIC_FAILURE", message="Синтетический сбой")]
    gateway = Gateway(profile, [])
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "EXTRACTION_MATERIAL_ERRORS" and gateway.requests == []


@pytest.mark.parametrize("field", ["request_hash", "prompt_hash", "normalization_hash", "model_id", "recording_set_version", "parameters"])
def test_all_provenance_is_checked_including_json_types(settings, profile, document, field):
    def alter(result):
        changed = "f" * 64 if field.endswith("hash") else "synthetic-other"
        if field == "parameters":
            changed = {**result.metadata.parameters, "flag": 0}  # bool False != JSON number 0
        result.metadata = result.metadata.model_copy(update={field: changed})
        return result
    gateway = Gateway(profile, [payload(document)], alter)
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "ANALYSIS_PROVENANCE_CONFLICT"


def test_live_never_accepts_authored_or_switches_to_replay(settings, profile, document):
    settings.app_model_mode = "live"
    gateway = Gateway(profile, [payload(document)])
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "ANALYSIS_PROVENANCE_CONFLICT" and len(gateway.requests) == 1


def test_real_replay_gateway_cannot_be_labelled_live(settings, profile, tmp_path):
    settings.app_model_mode = "live"
    gateway = ReplayGateway(tmp_path / "not-opened", profile.model_id, profile.parameters["normalization_hash"])
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings)
    assert exc.value.code == "ANALYSIS_MODE_CONFLICT"


def test_missing_gateway_metadata_is_an_explicit_safe_failure(settings, profile, document):
    gateway = Gateway(profile, [payload(document)], lambda result: GatewayResult.model_construct(value=result.value))
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "ANALYSIS_PROVENANCE_INVALID"


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_timeout_retry_budget_is_finite_and_safe(settings, document, attempts):
    profile = build_text_profile(settings, model_id="synthetic", recording_set_version="timeout-v1", max_attempts=attempts)
    gateway = Gateway(profile, [TimeoutError("SENSITIVE_SENTINEL")] * attempts)
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "ANALYSIS_TIMEOUT" and "SENSITIVE_SENTINEL" not in str(exc.value)
    assert len(gateway.requests) == attempts


def test_transient_timeout_recovers_without_fabricated_metadata(settings, profile, document):
    gateway = Gateway(profile, [TimeoutError(), payload(document)])
    result = build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert len(gateway.requests) == 2 and len(result.metadata) == 1


def test_unknown_replay_is_not_retried_or_turned_into_empty_success(settings, profile, document):
    gateway = Gateway(profile, [GatewayUnavailable("REPLAY_RECORD_MISSING", "SENSITIVE_SENTINEL")])
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "REPLAY_RECORD_MISSING" and len(gateway.requests) == 1
    assert "SENSITIVE_SENTINEL" not in str(exc.value)


@pytest.mark.parametrize("kind", ["prompt", "settings", "version", "normalization", "bool-attempts", "json-type"])
def test_stale_profile_and_settings_are_rejected_at_construction(settings, profile, kind):
    if kind == "prompt": profile.prompt_hashes["extract_needs"] = "f" * 64
    elif kind == "settings": settings.app_retrieval_k = 1
    elif kind == "version": profile.parameters["text_integration"]["version"] = "old"
    elif kind == "normalization": profile.parameters["normalization_hash"] = "f" * 64
    elif kind == "bool-attempts": profile.parameters["text_integration"]["max_attempts"] = True
    else: profile.parameters["text_integration"]["retrieval_k"] = 20.0
    with pytest.raises((GatewayUnavailable, RepositoryError)) as exc:
        build_text_pipeline(None, profile, settings)
    assert getattr(exc.value, "code", None) in {"ANALYSIS_PROFILE_INVALID", "ANALYSIS_PROFILE_CONFLICT", "NORMALIZATION_PROFILE_CONFLICT"}


def test_full_topic_json_limit_is_checked_before_gateway(settings, document):
    settings.app_passport_batch_max_chars = 1000
    profile = build_text_profile(settings, model_id="synthetic", recording_set_version="batch-v1")
    need = payload(document).needs[0]
    ref = EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)
    topic = Topic(topic_id="synthetic-large", title="Тест", summary="Синтетика " * 1000, need_ids=[], evidence=[ref],
        passport=TopicPassport(scenario="Связь", need_statement="Связаться", scope="Контакты", version=1,
            inclusion_examples=[PassportExample(text=need.evidence_quote, evidence_refs=[ref])]))
    gateway = Gateway(profile, [])
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.assign_topic(need, CatalogSnapshot(product_id="webneft", catalog_revision=1, topics=[topic]))
    assert exc.value.code == "ANALYSIS_BATCH_TOO_LARGE" and gateway.requests == []


def test_empty_catalog_remains_review_without_invented_topic(settings, profile, document):
    need = payload(document).needs[0]
    response = BatchAssignment(decision="provisional_no_match", reason="Синтетический пустой каталог",
        evidence_refs=[EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)])
    gateway = Gateway(profile, [response])
    result = build_text_pipeline(gateway, profile, settings).analysis.assign_topic(need,
        CatalogSnapshot(product_id="webneft", catalog_revision=0, topics=[]))
    assert result.assignment.decision == "review" and result.proposed_topic is None
    assert result.assignment.candidate_set_complete and len(result.metadata) == 1


def test_invalid_structured_response_retries_then_fails_explicitly(settings, profile, document):
    def invalid(result):
        return GatewayResult.model_construct(value={"needs": "invalid"}, metadata=result.metadata)
    gateway = Gateway(profile, [payload(document), payload(document)], invalid)
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert exc.value.code == "ANALYSIS_RESPONSE_INVALID" and len(gateway.requests) == 2


def test_invalid_json_from_gateway_has_bounded_retry(settings, profile, document):
    from json import JSONDecodeError
    gateway = Gateway(profile, [JSONDecodeError("Synthetic failure", "", 0), payload(document)])
    result = build_text_pipeline(gateway, profile, settings).analysis.extract_needs(document)
    assert len(result.needs) == 1 and len(gateway.requests) == 2


def test_integration_snapshots_mutable_settings_profile_and_request(settings, profile, document):
    original = document.model_dump()
    gateway = Gateway(profile.model_copy(deep=True), [payload(document)])
    adapters = build_text_pipeline(gateway, profile, settings)
    settings.app_passport_batch_max_chars = 1
    profile.parameters["flag"] = True
    result = adapters.analysis.extract_needs(document)
    assert len(result.needs) == 1 and document.model_dump() == original
    assert gateway.requests[0].parameters["flag"] is False


@pytest.mark.parametrize("parameters", [[], False, 0, {"normalization_hash": "f" * 64}, {"text_integration": {}}],
                         ids=["list", "bool", "number", "reserved-hash", "reserved-policy"])
def test_profile_builder_does_not_accept_ambiguous_or_reserved_parameters(settings, parameters):
    with pytest.raises(GatewayUnavailable) as exc:
        build_text_profile(settings, model_id="synthetic", recording_set_version="test-v1", parameters=parameters)
    assert exc.value.code == "ANALYSIS_PROFILE_INVALID"

def test_need_extractor_accepts_valid_evidence(profile, document):
    value = payload(document)
    gateway = Gateway(profile, [value])

    result = NeedExtractor(gateway, profile).extract(document)

    assert len(result.needs) == len(value.needs)
    assert all(need.need_id == "pending" for need in result.needs)
    assert len(result.metadata) == 1


@pytest.mark.parametrize(
    ("field", "error_code"),
    [
        ("source_id", "EXTRACTION_REFERENCE_INVALID"),
        ("fragment_id", "EXTRACTION_REFERENCE_INVALID"),
        ("evidence_quote", "EXTRACTION_QUOTE_INVALID"),
    ],
)
def test_need_extractor_rejects_forged_evidence(
    profile, document, field, error_code
):
    value = payload(document)
    setattr(value.needs[0], field, "synthetic-forged")

    gateway = Gateway(profile, [value])

    with pytest.raises(GatewayUnavailable) as exc:
        NeedExtractor(gateway, profile).extract(document)

    assert exc.value.code == error_code

