"""Строгое ASR/VL replay: immutable explicit JSON set, без сети/active fallback."""

from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4
import wave

from pydantic import Field, model_validator

from .gateways import ApprovedAsset, GatewayResult, GatewayUnavailable, MediaText
from .errors import RepositoryError
from .ingestion.normalizer import MASKING_WARNING, MAX_TEXT_BYTES, normalization_recipe
from .masking import mask_pii
from .processing_schemas import MediaComponentProfile, MediaProcessingProfile, MediaRequest
from .replay import canonical, semantic_hash
from .schemas import Contract, RecordingMeta, Sha256
from .security import CHUNK_BYTES, MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, checked_open, checked_path

MAX_SET_BYTES = 10 * 1024 * 1024


def media_recipe() -> dict:
    return {"version": "media-pii-v1", "implementation": "partial-media-normalizer-v1",
        "text_recipe": normalization_recipe(), "policy": "success-context-with-visible-material-errors-v1",
        "units": "nonempty-input-text-or-text-only-empty; each-asset",
        "fragments": "source/version/content/asset/segment/profile-stable-v1",
        "asr": "paired-strict-ms-within-actual-duration; null-without-timestamps",
        "mask": "before-persistence-recording-analysis; warning-preserved",
        "limits": {"fragments": 256, "text_bytes": MAX_TEXT_BYTES, "audio_seconds": 60}}


MEDIA_NORMALIZATION_HASH = semantic_hash(media_recipe())


def build_media_profile(*, asr: MediaComponentProfile, vision: MediaComponentProfile,
                        recording_set_version: str, mode="replay") -> MediaProcessingProfile:
    return MediaProcessingProfile(asr=asr, vision=vision, recording_set_version=recording_set_version,
                                  normalization_hash=MEDIA_NORMALIZATION_HASH, mode=mode)


def checked_media_profile(profile: MediaProcessingProfile) -> MediaProcessingProfile:
    profile = MediaProcessingProfile.model_validate(profile.model_dump())
    if profile.normalization_hash != MEDIA_NORMALIZATION_HASH:
        raise GatewayUnavailable("MEDIA_PROFILE_CONFLICT", "Media recipe не совпадает с профилем. Старые записи не изменялись.")
    return profile


def masked_media_text(value) -> MediaText:
    raw = value.model_dump() if isinstance(value, MediaText) else value
    if not isinstance(raw, dict) or not isinstance(raw.get("fragments"), list) or len(raw["fragments"]) > 256:
        raise ValueError("Invalid media fragment count")
    for fragment in raw["fragments"]:
        if not isinstance(fragment, dict) or any(fragment.get(k) is not None and type(fragment[k]) is not int for k in ("start_ms", "end_ms")):
            raise ValueError("Media timestamps must be strict integers")
    parsed = MediaText.model_validate(raw)
    if sum(len(f.text.encode("utf-8")) + len((f.warning or "").encode("utf-8")) for f in parsed.fragments) > MAX_TEXT_BYTES:
        raise ValueError("Media text too large")
    return MediaText(fragments=[f.model_copy(update={"text": mask_pii(f.text),
        "warning": mask_pii(f.warning) if f.warning else MASKING_WARNING}) for f in parsed.fragments])


def media_request(asset: ApprovedAsset, size: int, operation: str, profile: MediaProcessingProfile) -> MediaRequest:
    return MediaRequest(operation=operation, asset_id=asset.asset_id, content_hash=asset.content_hash,
        byte_size=size, mime_type=asset.mime_type, model=profile.asr if operation == "transcribe" else profile.vision,
        normalization_hash=profile.normalization_hash, recording_set_version=profile.recording_set_version)


def media_request_key(request: MediaRequest) -> str:
    request = MediaRequest.model_validate(request.model_dump())
    return semantic_hash({"format": "media-request-key-v1", "request": request.model_dump(mode="json"),
                          "response_schema": MediaText.model_json_schema()})


class MediaRecording(Contract):
    format_version: Literal["media-record-v1"] = "media-record-v1"
    request: MediaRequest
    schema_hash: Sha256
    response: MediaText
    metadata: RecordingMeta

    @model_validator(mode="before")
    @classmethod
    def strict_timestamps(cls, value):
        if isinstance(value, dict) and "response" in value:
            # Проверяем до приведения legacy Fragment int-полей Pydantic.
            masked_media_text(value["response"])
        return value


def validate_media_record(record: MediaRecording, profile: MediaProcessingProfile) -> MediaText:
    profile = checked_media_profile(profile)
    request, meta = record.request, record.metadata
    model = profile.asr if request.operation == "transcribe" else profile.vision
    prompt_hash = sha256(model.prompt.encode("utf-8")).hexdigest() if model.prompt else None
    if (request.model != model or request.normalization_hash != profile.normalization_hash
            or request.recording_set_version != profile.recording_set_version
            or record.schema_hash != semantic_hash(MediaText.model_json_schema())
            or meta.request_hash != media_request_key(request) or meta.normalization_hash != profile.normalization_hash
            or meta.model_id != model.model_id or meta.parameters != model.parameters or meta.prompt_hash != prompt_hash
            or meta.recording_set_version != profile.recording_set_version
            or profile.mode == "live" and meta.origin != "model"):
        raise GatewayUnavailable("MEDIA_RECORD_INVALID", "Схема, профиль, hashes или provenance media-записи не совпадают.")
    if request.byte_size > (MAX_AUDIO_BYTES if request.operation == "transcribe" else MAX_IMAGE_BYTES):
        raise GatewayUnavailable("MEDIA_RECORD_INVALID", "Размер media-запроса превышает проверенный предел.")
    value = masked_media_text(record.response)
    kind = "asr" if request.operation == "transcribe" else "vision"
    if (value != record.response or any(f.kind != kind or f.asset_id != request.asset_id
            or f.end_ms is not None and f.end_ms > 60000 for f in value.fragments)
            or len({f.fragment_id for f in value.fragments}) != len(value.fragments)):
        raise GatewayUnavailable("MEDIA_RECORD_INVALID", "Текст не замаскирован, либо фрагменты не соответствуют media-запросу.")
    return value


def make_media_record(request: MediaRequest, response, profile: MediaProcessingProfile, *, origin: str) -> MediaRecording:
    profile = checked_media_profile(profile)
    value = masked_media_text(response)
    model = request.model
    record = MediaRecording(request=request, response=value, schema_hash=semantic_hash(MediaText.model_json_schema()),
        metadata=RecordingMeta(request_hash=media_request_key(request),
            prompt_hash=sha256(model.prompt.encode("utf-8")).hexdigest() if model.prompt else None,
            normalization_hash=profile.normalization_hash, model_id=model.model_id, parameters=model.parameters,
            origin=origin, recording_set_version=profile.recording_set_version))
    validate_media_record(record, profile)
    return record


def read_checked_json(path: Path, limit=MAX_SET_BYTES):
    with checked_open(path, path.parent) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON exceeds bound")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("Nonfinite JSON value")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)


def write_immutable_json(path: Path, value) -> None:
    """Публикует полные bytes EXCL; прежний путь никогда не заменяется."""
    path = Path(os.path.abspath(path))
    checked_path(path, Path(path.anchor))
    encoded = canonical(value).encode("utf-8")
    if len(encoded) > MAX_SET_BYTES:
        raise ValueError("JSON exceeds bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link обеспечивает EXCL и на POSIX, где rename может затереть target.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if canonical(read_checked_json(path)) != canonical(value):
                raise GatewayUnavailable("MEDIA_RECORDING_CONFLICT", "Файл уже опубликован с другим содержимым. Выберите новую версию и путь.") from None
    finally:
        temporary.unlink(missing_ok=True)


class MediaRecordingSet(Contract):
    format_version: Literal["media-recordings-v1"] = "media-recordings-v1"
    profile: MediaProcessingProfile
    profile_hash: Sha256
    records: dict[str, MediaRecording] = Field(min_length=1, max_length=1000)
    checksums: dict[str, Sha256]

    @model_validator(mode="after")
    def verify(self):
        checked_media_profile(self.profile)
        if self.profile_hash != semantic_hash(self.profile.model_dump(mode="json")) or self.records.keys() != self.checksums.keys():
            raise ValueError("Media set profile/index mismatch")
        for key, record in self.records.items():
            validate_media_record(record, self.profile)
            if key != record.metadata.request_hash or self.checksums[key] != semantic_hash(record.model_dump(mode="json")):
                raise ValueError("Media record checksum mismatch")
        return self


def publish_media_recordings(path: Path, records: list[MediaRecording], profile: MediaProcessingProfile) -> dict:
    unique = {}
    for record in records:
        validate_media_record(record, profile)
        key = record.metadata.request_hash
        if key in unique and unique[key] != record:
            raise GatewayUnavailable("MEDIA_RECORDING_CONFLICT", "Разные ответы одного media-запроса недопустимы.")
        unique[key] = record
    bundle = MediaRecordingSet(profile=profile, profile_hash=semantic_hash(profile.model_dump(mode="json")), records=unique,
        checksums={k: semantic_hash(v.model_dump(mode="json")) for k, v in unique.items()})
    write_immutable_json(path, bundle.model_dump(mode="json"))
    verified = MediaRecordingSet.model_validate(read_checked_json(path))
    if verified != bundle:
        raise GatewayUnavailable("MEDIA_RECORDING_CONFLICT", "Опубликованные bytes не совпадают; набор не используется.")
    return {"record_count": len(unique), "origins": sorted({r.metadata.origin for r in unique.values()}), "network_used": False}


def asset_details(asset: ApprovedAsset, root: Path) -> tuple[int, int | None]:
    """Безопасно перечитывает точные bytes перед replay/cache; duration из файла."""
    limit = MAX_AUDIO_BYTES if asset.mime_type.startswith("audio/") else MAX_IMAGE_BYTES
    digest, size = sha256(), 0
    with checked_open(asset.absolute_path, root) as stream:
        while chunk := stream.read(min(CHUNK_BYTES, limit + 1 - size)):
            size += len(chunk)
            if size > limit:
                raise GatewayUnavailable("MEDIA_FILE_TOO_LARGE", "Материал превышает проверенный предел.")
            digest.update(chunk)
        if not size or digest.hexdigest() != asset.content_hash:
            raise GatewayUnavailable("MEDIA_FILE_CHANGED", "Материал изменён или пуст; повторите проверку источника.")
        stream.seek(0)
        seconds = None
        try:
            if asset.mime_type == "audio/wav":
                with wave.open(stream, "rb") as audio:
                    seconds = audio.getnframes() / audio.getframerate()
            elif asset.mime_type == "audio/mpeg":
                from mutagen.mp3 import MP3
                seconds = MP3(stream).info.length
        except Exception:
            raise GatewayUnavailable("MEDIA_FILE_INVALID", "Не удалось проверить длительность аудио.") from None
        if seconds is not None and (not math.isfinite(seconds) or not 0 < seconds <= 60):
            raise GatewayUnavailable("MEDIA_FILE_INVALID", "Длительность аудио вне проверенных пределов.")
        return size, math.ceil(seconds * 1000) if seconds is not None else None


class MediaReplayGateway:
    """Реализует AsrGateway/VisionGateway только для явного mode=replay."""
    def __init__(self, path: Path, profile: MediaProcessingProfile, *, allowed_root: Path):
        self.path, self.profile, self.allowed_root = path, checked_media_profile(profile), allowed_root
        if profile.mode != "replay":
            raise GatewayUnavailable("MEDIA_LIVE_NOT_CONNECTED", "Live ASR/VL не подключён; replay не подставляется автоматически.")

    def records(self) -> MediaRecordingSet:
        try:
            bundle = MediaRecordingSet.model_validate(read_checked_json(self.path))
            if bundle.profile != self.profile:
                raise GatewayUnavailable("MEDIA_REPLAY_PROFILE_CONFLICT", "Настроен другой media-профиль; выберите точный набор.")
            return bundle
        except GatewayUnavailable:
            raise
        except Exception:
            raise GatewayUnavailable("MEDIA_REPLAY_SET_INVALID", "Media-набор отсутствует или повреждён. Проверьте recordings/profile.") from None

    def _generate(self, asset, operation):
        try:
            size, duration = asset_details(asset, self.allowed_root)
            request = media_request(asset, size, operation, self.profile)
        except GatewayUnavailable:
            raise
        except RepositoryError as exc:
            raise GatewayUnavailable(exc.code, exc.message) from None
        except (ValueError, TypeError, OSError):
            raise GatewayUnavailable("MEDIA_REQUEST_INVALID", "Материал или операция не соответствует проверенному media-контракту.") from None
        bundle = self.records()
        record = bundle.records.get(media_request_key(request))
        if record is None:
            raise GatewayUnavailable("REPLAY_RECORD_MISSING", "Точной media-записи нет; live автоматически не вызывается.")
        value = validate_media_record(record, self.profile)
        if duration is not None and any(f.end_ms is not None and f.end_ms > duration for f in value.fragments):
            raise GatewayUnavailable("MEDIA_TIMESTAMPS_INVALID", "ASR-интервал выходит за реальную длительность записи.")
        return GatewayResult(value=value, metadata=record.metadata)

    def transcribe(self, asset: ApprovedAsset) -> GatewayResult[MediaText]:
        return self._generate(asset, "transcribe")

    def describe(self, asset: ApprovedAsset) -> GatewayResult[MediaText]:
        return self._generate(asset, "describe")
