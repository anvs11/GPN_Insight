"""Проверенный стык классификации У2; тот же точный text replay, без keyword rules."""

from hashlib import sha256
from pathlib import Path

from .assessment_schemas import AssessmentResult, NeedAssessment
from .gateways import GatewayUnavailable, TextRequest
from .replay import canonical, request_key
from .masking import mask_pii

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts/classify.md"
PROMPT_VERSION = "classify-need-v1"


def assessment_request(need, view, profile):
    return TextRequest(operation="classify_need", prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        prompt_version=PROMPT_VERSION, parameters=profile.parameters,
        data={"need": need.model_dump(mode="json"), "document": view.document.model_dump(mode="json"),
              "analysis_digest": view.analysis_digest})


class NeedAssessor:
    def __init__(self, gateway, profile, mode):
        self.gateway, self.profile, self.mode = gateway, profile, mode

    def assess(self, need, view):
        request = assessment_request(need, view, self.profile)
        result = self.gateway.generate(request, NeedAssessment)
        value = NeedAssessment.model_validate(result.value.model_dump())
        meta = result.metadata
        if (meta.request_hash != request_key(request, NeedAssessment, self.profile.model_id, self.profile.parameters["normalization_hash"])
                or meta.prompt_hash != sha256(request.prompt.encode()).hexdigest()
                or meta.prompt_hash != self.profile.prompt_hashes.get("classify_need")
                or meta.model_id != self.profile.model_id or meta.normalization_hash != self.profile.parameters["normalization_hash"]
                or meta.recording_set_version != self.profile.recording_set_version
                or canonical(meta.parameters) != canonical(self.profile.parameters)
                or self.mode == "live" and meta.origin != "model"):
            raise GatewayUnavailable("ASSESSMENT_PROVENANCE_CONFLICT", "Оценка не соответствует точному запросу/профилю.")
        checked_assessment(AssessmentResult(need_id=need.need_id, analysis_digest=view.analysis_digest,
                                           assessment=value, metadata=meta), need, view)
        return AssessmentResult(need_id=need.need_id, analysis_digest=view.analysis_digest, assessment=value, metadata=meta)


def checked_assessment(result, need, view):
    result = AssessmentResult.model_validate(result.model_dump())
    if any(mask_pii(text) != text for text in [result.assessment.reason, *result.assessment.uncertainties]):
        raise GatewayUnavailable("ASSESSMENT_UNSAFE", "Пояснение оценки содержит распознаваемые немаскированные данные; результат не принят.")
    fragments = {f.fragment_id: f for f in view.document.fragments}
    if result.need_id != need.need_id or result.analysis_digest != view.analysis_digest or not any(
            ref.fragment_id == need.fragment_id for ref in result.assessment.evidence_refs):
        raise GatewayUnavailable("ASSESSMENT_EVIDENCE_INVALID", "Оценка относится к другой потребности или ревизии.")
    for ref in result.assessment.evidence_refs:
        if (ref.source_id != need.source_id or ref.fragment_id not in fragments
                or ref.quote not in fragments[ref.fragment_id].text):
            raise GatewayUnavailable("ASSESSMENT_EVIDENCE_INVALID", "Основание оценки не подтверждено пригодным фрагментом.")
    return result
