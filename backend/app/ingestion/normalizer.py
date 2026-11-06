"""Текстовая нормализация без чтения источников, записи в БД и вызовов моделей."""

from pydantic import TypeAdapter

from ..errors import RepositoryError
from ..masking import mask_pii, masking_recipe
from ..replay import canonical, semantic_hash
from ..schemas import Fragment, MaterialError, NormalizedDocument, ProcessingProfile, Sha256, TicketInput, Version

NORMALIZATION_VERSION = "text-pii-v1-proposal"
MAX_TEXT_BYTES = 1024 * 1024
MASKING_WARNING = "Маскирование не гарантирует обезличивание: имена и адреса могут сохраниться. Используйте синтетические или разрешённые данные."


def normalization_recipe() -> dict[str, object]:
    return {"version": NORMALIZATION_VERSION, "implementation": "text-normalizer-v1",
        "masking": masking_recipe(), "text_encoding": "UTF-8", "max_text_bytes": MAX_TEXT_BYTES,
        "whitespace_and_unicode": "preserve-except-masked-spans", "fragments": "one-text-fragment; local-id=text-1",
        "fragment_warning": MASKING_WARNING,
        "empty_text": "one-empty-text-fragment", "media": "reject-whole-ticket",
        "source_identity": "ticket-product/source; pipeline-content_hash/source_version"}


NORMALIZATION_HASH = semantic_hash(normalization_recipe())


def normalize_ticket(ticket: TicketInput, *, content_hash: str, source_version: int) -> NormalizedDocument:
    try:
        if not isinstance(content_hash, str):
            raise ValueError("SHA-256 string required")
        TypeAdapter(Sha256).validate_python(content_hash)
        TypeAdapter(Version).validate_python(source_version)
    except (TypeError, ValueError):
        raise RepositoryError("NORMALIZATION_CONTEXT_INVALID", "Передайте SHA-256 и положительную целую source_version из контекста pipeline.", 422) from None
    try:
        if not isinstance(ticket, TicketInput):
            raise ValueError("TicketInput required")
        ticket = TicketInput.model_validate(ticket.model_dump(warnings=False))
        ticket.source_id.encode("utf-8")
        ticket.product_id.encode("utf-8")
    except (TypeError, ValueError):
        raise RepositoryError("NORMALIZATION_INPUT_INVALID", "Передайте корректный TicketInput. Источник не изменён.", 422) from None

    def document(*, fragments=None, errors=None):
        return NormalizedDocument(product_id=ticket.product_id, source_id=ticket.source_id,
            content_hash=content_hash, version=source_version, normalization_version=NORMALIZATION_VERSION,
            fragments=fragments or [], errors=errors or [])

    def failure(code, message, asset_id=None):
        return MaterialError(source_id=ticket.source_id, asset_id=asset_id, stage="normalization", code=code, message=message)

    if ticket.product_id != "webneft":
        return document(errors=[failure("NORMALIZATION_PRODUCT_UNSUPPORTED", "В пилоте поддержан только product_id=webneft.")])
    if ticket.assets:
        return document(errors=[failure("MEDIA_NORMALIZATION_NOT_SUPPORTED", "Медиа ещё не подключены. Передайте текстовый набор без вложений.", a.asset_id) for a in ticket.assets])
    if len(ticket.text) > MAX_TEXT_BYTES:
        return document(errors=[failure("NORMALIZATION_TEXT_TOO_LARGE", "Текст превышает 1 МиБ. Разделите обращение перед обработкой.")])
    try:
        raw = ticket.text.encode("utf-8")
    except UnicodeError:
        return document(errors=[failure("NORMALIZATION_UTF8_INVALID", "Текст содержит некорректный Unicode. Исправьте источник и повторите обработку.")])
    if len(raw) > MAX_TEXT_BYTES:
        return document(errors=[failure("NORMALIZATION_TEXT_TOO_LARGE", "Текст превышает 1 МиБ UTF-8. Разделите обращение перед обработкой.")])
    try:
        masked = mask_pii(ticket.text)
        if not isinstance(masked, str):
            raise TypeError("Masking must return text")
        masked.encode("utf-8")
        fragment = Fragment(fragment_id="text-1", kind="text", text=masked, warning=MASKING_WARNING)
    except Exception:
        return document(errors=[failure("NORMALIZATION_MASKING_FAILED", "Маскирование не завершено. Проверьте правила; фрагмент не опубликован.")])
    return document(fragments=[fragment])


class TextNormalizer:
    def __init__(self, profile: ProcessingProfile):
        try:
            if not isinstance(profile, ProcessingProfile):
                raise ValueError("ProcessingProfile required")
            profile = ProcessingProfile.model_validate(profile.model_dump(warnings=False))
            canonical(profile.model_dump()).encode("utf-8")
        except (TypeError, ValueError):
            raise RepositoryError("NORMALIZATION_PROFILE_INVALID", "Передайте корректный ProcessingProfile для текстовой нормализации.", 422) from None
        if (profile.normalization_version != NORMALIZATION_VERSION or
            profile.parameters.get("normalization_hash") != NORMALIZATION_HASH):
            raise RepositoryError("NORMALIZATION_PROFILE_CONFLICT", "Версия или normalization_hash не совпадают с реализованными правилами. Обновите профиль; старые записи не переписывайте.", 422)

    @property
    def normalization_version(self) -> str:
        return NORMALIZATION_VERSION

    @property
    def normalization_hash(self) -> str:
        return NORMALIZATION_HASH

    def normalize_ticket(self, ticket: TicketInput, *, content_hash: str, source_version: int) -> NormalizedDocument:
        return normalize_ticket(ticket, content_hash=content_hash, source_version=source_version)
