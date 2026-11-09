"""Read-only привязка утверждённого профиля к настоящему серверному gateway."""

import json

from .config import Settings
from .gateways import GatewayUnavailable, TextGateway
from .ingestion.integration import PROMPT_VERSIONS, build_text_pipeline, build_text_profile
from .ingestion.normalizer import NORMALIZATION_HASH
from .pipeline import PipelineAdapters
from .replay import ReplayGateway, canonical
from .repositories import RepositoryError
from .schemas import ProcessingProfile

MAX_PROFILE_BYTES = 64 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate profile key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite profile number")


def _load_profile(settings: Settings) -> ProcessingProfile:
    path = settings.repo_path(settings.app_processing_profile_path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PROFILE_BYTES + 1)
    except OSError:
        raise GatewayUnavailable("PROCESSING_PROFILE_UNAVAILABLE", "Не удалось прочитать утверждённый профиль обработки.") from None
    if len(raw) > MAX_PROFILE_BYTES:
        raise GatewayUnavailable("PROCESSING_PROFILE_TOO_LARGE", "Профиль обработки превышает 64 КиБ.")
    try:
        # Серверный config допускает UTF-8 BOM из Windows-редакторов;
        # строгая проверка пользовательского manifest этим не меняется.
        values = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        stored = ProcessingProfile.model_validate(values)
        canonical(stored.model_dump()).encode("utf-8")
        parameters = {k: v for k, v in stored.parameters.items() if k not in {"normalization_hash", "text_integration", "partial_media"}}
        attempts = stored.parameters["text_integration"]["max_attempts"]
        if "partial_media" in stored.parameters:
            from .partial_integration import build_partial_profile
            from .processing_schemas import MediaProcessingProfile
            rebuilt = build_partial_profile(settings,
                media_profile=MediaProcessingProfile.model_validate(stored.parameters["partial_media"]["media_profile"]),
                model_id=stored.model_id, recording_set_version=stored.recording_set_version,
                parameters=parameters, max_attempts=attempts)
        else:
            rebuilt = build_text_profile(settings, model_id=stored.model_id,
                recording_set_version=stored.recording_set_version, parameters=parameters, max_attempts=attempts)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise GatewayUnavailable("PROCESSING_PROFILE_INVALID", "Профиль не соответствует ProcessingProfile и текущей политике обработки.") from None
    # Не исправляем старые хеши/лимиты и JSON-типы под текущую реализацию молча.
    if canonical(stored.model_dump(mode="json")) != canonical(rebuilt.model_dump(mode="json")):
        raise GatewayUnavailable("PROCESSING_PROFILE_CONFLICT", "Утверждённый профиль не совпадает с текущими промптами, нормализацией или настройками.")
    return rebuilt


def _validate_replay(gateway: ReplayGateway, profile: ProcessingProfile, settings: Settings) -> None:
    if (settings.app_replay_model_id != profile.model_id or
        settings.app_replay_normalization_hash != profile.parameters["normalization_hash"]):
        raise GatewayUnavailable("REPLAY_PROFILE_CONFLICT", "Настройки replay не совпадают с утверждённым профилем.")
    records = [record for record in gateway.records().values() if
        record.metadata.model_id == profile.model_id and record.metadata.normalization_hash == profile.parameters["normalization_hash"]]
    if not records:
        raise GatewayUnavailable("REPLAY_RECORD_MISSING", "В наборе нет записей настроенной модели и нормализации.")
    for record in records:
        meta = record.metadata
        if (meta.recording_set_version != profile.recording_set_version or
            meta.prompt_hash != profile.prompt_hashes[record.request.operation] or
            record.request.prompt_version != PROMPT_VERSIONS[record.request.operation] or
            canonical(meta.parameters) != canonical(profile.parameters) or
            canonical(record.request.parameters) != canonical(profile.parameters)):
            raise GatewayUnavailable("REPLAY_PROFILE_CONFLICT", "Записи replay относятся к другому профилю, промптам, параметрам или версии набора.")


def configured_text_pipeline(settings: Settings, gateway: TextGateway) -> tuple[PipelineAdapters | None, str | None]:
    """Не создаёт gateway/фикстуры, не пишет файлы и не вызывает generate()."""
    if settings.app_processing_profile_path is None:
        return None, "PROCESSING_PROFILE_NOT_CONFIGURED" if isinstance(gateway, ReplayGateway) else None
    if settings.app_model_mode == "live":
        # Текущая production-фабрика ещё не содержит внутреннего live-адаптера.
        # Наличие URL/ID/профиля не разрешает подменить его replay.
        return None, "MODEL_ADAPTER_NOT_IMPLEMENTED"
    if not isinstance(gateway, ReplayGateway):
        return None, "REPLAY_NOT_CONFIGURED"
    try:
        profile = _load_profile(settings)
        _validate_replay(gateway, profile, settings)
        if "partial_media" in profile.parameters:
            from .partial_integration import build_partial_pipeline
            from .repositories import Repository
            return build_partial_pipeline(gateway, profile, settings, Repository(settings.repo_path(settings.app_db_path))), None
        return build_text_pipeline(gateway, profile, settings), None
    except (GatewayUnavailable, RepositoryError) as exc:
        return None, exc.code
    except (OSError, TypeError, ValueError, RuntimeError):
        return None, "PROCESSING_PROFILE_INVALID"
