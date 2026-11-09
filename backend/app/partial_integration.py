"""Явный production bootstrap media replay и проверенного анализа У2."""

from hashlib import sha256

from .adapters.text import build_analysis
from .assessment import NeedAssessor, PROMPT_PATH
from .gateways import GatewayUnavailable
from .ingestion.integration import (
    _CheckedAnalysis, _CheckedGateway, _prompts, _proposal_prompt_path, build_text_profile,
    DEFAULT_ASSIGN_PROMPT_PATH,
)
from .ingestion.media_normalizer import MediaNormalizer
from .media_replay import MediaReplayGateway, checked_media_profile
from .pipeline import PipelineAdapters
from .replay import canonical, semantic_hash
from .schemas import ProcessingProfile


def build_partial_profile(settings, *, media_profile, model_id, recording_set_version, parameters=None, max_attempts=2):
    media_profile = checked_media_profile(media_profile)
    legacy = build_text_profile(settings, model_id=model_id, recording_set_version=recording_set_version,
                                parameters=parameters, max_attempts=max_attempts)
    values = {**legacy.parameters, "normalization_hash": media_profile.normalization_hash,
        "partial_media": {"policy": "partial-media-v1", "assessment_version": "need-assessment-v1",
                          "media_profile": media_profile.model_dump(mode="json"),
                          "media_profile_hash": semantic_hash(media_profile.model_dump(mode="json"))}}
    return ProcessingProfile(**{**legacy.model_dump(), "normalization_version": media_profile.normalization_version,
        "parameters": values, "prompt_hashes": {**legacy.prompt_hashes,
            "classify_need": sha256(PROMPT_PATH.read_text(encoding="utf-8").encode("utf-8")).hexdigest()}})


def build_partial_pipeline(gateway, profile, settings, repository):
    from .processing_schemas import MediaProcessingProfile
    if settings.app_model_mode != "replay" or settings.app_media_recordings_path is None:
        raise GatewayUnavailable("MEDIA_REPLAY_NOT_CONFIGURED", "Нужны явные replay профиль и путь media recordings; live не подключён.")
    media = MediaProcessingProfile.model_validate(profile.parameters["partial_media"]["media_profile"])
    parameters = {k: v for k, v in profile.parameters.items() if k not in {"normalization_hash", "text_integration", "partial_media"}}
    rebuilt = build_partial_profile(settings, media_profile=media, model_id=profile.model_id,
        recording_set_version=profile.recording_set_version, parameters=parameters,
        max_attempts=profile.parameters["text_integration"]["max_attempts"])
    if canonical(rebuilt.model_dump()) != canonical(profile.model_dump()) or media.mode != settings.app_model_mode:
        raise GatewayUnavailable("PROCESSING_PROFILE_CONFLICT", "Partial профиль не совпадает с текущими правилами/промптами.")
    media_gateway = MediaReplayGateway(settings.repo_path(settings.app_media_recordings_path), media,
                                      allowed_root=settings.repo_path(settings.app_upload_root))
    media_gateway.records()  # При повреждении конфигурации не объявляем Pipeline готовым.
    normalizer = MediaNormalizer(repository, settings, media, asr_gateway=media_gateway, vision_gateway=media_gateway,
        cache_root=settings.repo_path(settings.app_media_cache_root))
    prompts = {**_prompts(), "classify_need": PROMPT_PATH.read_text(encoding="utf-8")}
    checked = _CheckedGateway(gateway, profile, settings, prompts)
    analysis = build_analysis(checked, profile, settings, mode=settings.app_model_mode,
        assign_prompt_path=DEFAULT_ASSIGN_PROMPT_PATH, propose_prompt_path=_proposal_prompt_path())
    return PipelineAdapters(normalizer=normalizer, analysis=_CheckedAnalysis(analysis, profile), profile=profile,
                            partial=True, assessor=NeedAssessor(checked, profile, settings.app_model_mode))
