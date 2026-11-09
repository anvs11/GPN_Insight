"""Самостоятельная partial/media нормализация; Pipeline A4 ещё не подключён."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import re
import sqlite3

from ..config import Settings
from ..errors import RepositoryError
from ..gateways import GatewayResult, GatewayUnavailable, MediaText
from ..media_replay import (
    MediaRecording, asset_details, checked_media_profile, masked_media_text, media_request,
    media_request_key, read_checked_json, validate_media_record, write_immutable_json,
)
from ..media_runtime import resolve_registered_asset
from ..processing_schemas import (
    MaterialOutcome, MediaProcessingProfile, NormalizationResult, SourceContext, make_normalization_result,
)
from ..replay import semantic_hash, stable_id
from ..repositories import Repository
from ..schemas import DatasetRegistration, MaterialError, TicketInput
from ..storage import source_content_hash
from ..security import MAX_MEDIA
from .normalizer import MAX_TEXT_BYTES, normalize_ticket


class MediaNormalizer:
    def __init__(self, repository: Repository, settings: Settings, profile: MediaProcessingProfile,
                 *, asr_gateway, vision_gateway, cache_root: Path | None = None):
        self.repository, self.settings = repository, settings
        self.profile = checked_media_profile(profile)
        self.profile_hash = semantic_hash(self.profile.model_dump(mode="json"))
        self.asr, self.vision, self.cache_root = asr_gateway, vision_gateway, cache_root
        if self.profile.mode != "replay":
            raise GatewayUnavailable("MEDIA_LIVE_NOT_CONNECTED", "Live ASR/VL ещё не подключён. Нельзя подставлять replay.")

    def _context(self, ticket, content_hash, source_version):
        self.repository.preview_dataset_counts(DatasetRegistration(dataset_id="normalization-context", synthetic=True, tickets=[]))
        try:
            with closing(sqlite3.connect(self.repository.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                snapshot = Repository(self.repository.path, _db=db)
                source = snapshot.get_source(ticket.source_id, ticket.product_id, source_version)
                materials = snapshot.source_materials(ticket.source_id, ticket.product_id, source_version)
            context = SourceContext(product_id=ticket.product_id, source_id=ticket.source_id,
                source_version=source_version, content_hash=content_hash, synthetic=source.synthetic, data_origin=source.data_origin)
            if source.ticket != ticket or source.content_hash != content_hash:
                raise RepositoryError("NORMALIZATION_IDENTITY_CONFLICT", "Передайте неизменённый источник и его точную версию.", 422)
            if ticket.product_id != "webneft":
                raise RepositoryError("NORMALIZATION_PRODUCT_UNSUPPORTED", "В пилоте поддержан только Вебнефть.", 422)
            if ticket.assets and not source.synthetic:
                raise RepositoryError("MEDIA_SYNTHETIC_REQUIRED", "Media replay пилота принимает только явно синтетические материалы.", 403)
            by_id = {m.asset_id: m for m in materials}
            if (len(by_id) != len(materials) or by_id.keys() != {a.asset_id for a in ticket.assets}
                    or source_content_hash(ticket, by_id) != content_hash
                    or any((by_id[a.asset_id].kind, by_id[a.asset_id].mime_type) != (a.kind, a.mime_type) for a in ticket.assets)):
                raise ValueError("Material registry mismatch")
            return context, by_id
        except (sqlite3.Error, ValueError):
            raise RepositoryError("NORMALIZATION_CONTEXT_INVALID", "Не удалось проверить точный контекст источника/материалов.", 422) from None

    def _fragments(self, value: MediaText, context: SourceContext, asset_id):
        return [f.model_copy(update={"fragment_id": stable_id("media-fragment-v1", context.product_id,
            context.source_id, context.source_version, context.content_hash, asset_id, self.profile_hash, i)})
            for i, f in enumerate(value.fragments)]

    def _checked_outcome(self, outcome, request, duration, context):
        outcome = MaterialOutcome.model_validate(outcome.model_dump())
        if outcome.state == "failed" or outcome.input_hash != request.content_hash:
            raise ValueError("Invalid success checkpoint")
        value = MediaText(fragments=outcome.fragments)
        if value != masked_media_text(value) or self._fragments(value, context, request.asset_id) != outcome.fragments:
            raise ValueError("Changed checkpoint fragments")
        if duration is not None and any(f.end_ms is not None and f.end_ms > duration for f in value.fragments):
            raise GatewayUnavailable("MEDIA_TIMESTAMPS_INVALID", "Интервал ASR выходит за длительность записи.")
        record = MediaRecording(request=request, response=value, schema_hash=semantic_hash(MediaText.model_json_schema()), metadata=outcome.metadata)
        validate_media_record(record, self.profile)
        return outcome

    def _media(self, asset_input, context, material):
        asset = resolve_registered_asset(self.repository, self.settings, product_id=context.product_id,
            source_id=context.source_id, source_version=context.source_version, asset_id=asset_input.asset_id)
        size, duration = asset_details(asset, self.settings.repo_path(self.settings.app_upload_root))
        operation = "transcribe" if asset_input.kind == "audio" else "describe"
        request = media_request(asset, size, operation, self.profile)
        key = semantic_hash({"format": "media-checkpoint-v1", "context": context.model_dump(mode="json"),
                             "profile_hash": self.profile_hash, "request_hash": media_request_key(request)})
        cache_path = self.cache_root / (key + ".json") if self.cache_root is not None else None
        if cache_path is not None and (cache_path.exists() or cache_path.is_symlink()):
            try:
                saved = read_checked_json(cache_path)
                outcome = MaterialOutcome.model_validate(saved["outcome"])
                if saved.keys() != {"format", "key", "checksum", "outcome"} or saved["format"] != 1 or saved["key"] != key or saved["checksum"] != semantic_hash(outcome.model_dump(mode="json")):
                    raise ValueError("Checkpoint binding mismatch")
                return self._checked_outcome(outcome, request, duration, context)
            except Exception:
                raise GatewayUnavailable("MEDIA_CHECKPOINT_INVALID", "Успешный media checkpoint повреждён; он сохранён для проверки.") from None
        result = self.asr.transcribe(asset) if operation == "transcribe" else self.vision.describe(asset)
        result = GatewayResult[MediaText].model_validate(result.model_dump())
        value = masked_media_text(result.value)
        record = MediaRecording(request=request, response=value, metadata=result.metadata,
                                schema_hash=semantic_hash(MediaText.model_json_schema()))
        validate_media_record(record, self.profile)
        outcome = MaterialOutcome(asset_id=asset.asset_id, kind=asset_input.kind, input_hash=material.content_hash,
            state="success" if any(f.text.strip() for f in value.fragments) else "empty",
            fragments=self._fragments(value, context, asset.asset_id), metadata=result.metadata)
        outcome = self._checked_outcome(outcome, request, duration, context)
        if cache_path is not None:
            write_immutable_json(cache_path, {"format": 1, "key": key, "outcome": outcome.model_dump(mode="json"),
                                             "checksum": semantic_hash(outcome.model_dump(mode="json"))})
        return outcome

    def normalize_ticket(self, ticket: TicketInput, *, content_hash: str, source_version: int) -> NormalizationResult:
        ticket = TicketInput.model_validate(ticket.model_dump())
        if len(ticket.assets) > MAX_MEDIA:
            raise RepositoryError("MEDIA_FILE_LIMIT_EXCEEDED", "В пилоте поддержано не более 30 вложений источника.", 413)
        context, materials = self._context(ticket, content_hash, source_version)
        outcomes = []
        if ticket.text or not ticket.assets:
            local = normalize_ticket(ticket.model_copy(update={"assets": []}), content_hash=content_hash, source_version=source_version)
            input_hash = sha256(ticket.text.encode("utf-8", errors="surrogatepass")).hexdigest()
            if local.errors:
                outcomes.append(MaterialOutcome(kind="text", input_hash=input_hash, state="failed", error=local.errors[0]))
            else:
                fragments = self._fragments(MediaText(fragments=local.fragments), context, None)
                outcomes.append(MaterialOutcome(kind="text", input_hash=input_hash,
                    state="success" if any(f.text.strip() for f in fragments) else "empty", fragments=fragments))
        for asset in sorted(ticket.assets, key=lambda a: a.asset_id):
            try:
                outcome = self._media(asset, context, materials[asset.asset_id])
                if (sum(len(f.text.encode("utf-8")) for m in [*outcomes, outcome] for f in m.fragments) > MAX_TEXT_BYTES
                        or sum(len(m.fragments) for m in [*outcomes, outcome]) > 256):
                    raise GatewayUnavailable("MEDIA_TEXT_TOO_LARGE", "Успешный текст превышает проверенный предел.")
            except Exception as exc:
                code = exc.code if isinstance(exc, (GatewayUnavailable, RepositoryError)) else "MEDIA_NORMALIZATION_FAILED"
                if not isinstance(code, str) or re.fullmatch(r"[A-Z0-9_]{1,80}", code) is None:
                    code = "MEDIA_NORMALIZATION_FAILED"
                outcome = MaterialOutcome(asset_id=asset.asset_id, kind=asset.kind, input_hash=materials[asset.asset_id].content_hash,
                    state="failed", error=MaterialError(source_id=context.source_id, asset_id=asset.asset_id,
                    stage="asr" if asset.kind == "audio" else "vision", code=code,
                    message="Материал не обработан. Проверьте код ошибки; пригодные успешные части сохранены."))
            outcomes.append(outcome)
        return make_normalization_result(context, self.profile_hash, self.profile.normalization_version, outcomes)
