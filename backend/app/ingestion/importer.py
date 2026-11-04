"""Строгий текстовый manifest и preview без постоянных изменений."""

import json
import math
from collections.abc import Collection
from datetime import datetime

from pydantic import ValidationError

from ..config import Settings
from ..repositories import Repository, RepositoryError
from ..schemas import DatasetRegistration, ImportCounts, ImportIssue, ImportPreview, TicketInput

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_TICKETS = 100


class ManifestError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        self.issue = ImportIssue(code=code, message=message, field=field)
        super().__init__(message)


def _check_depth(text: str) -> None:
    # Проверяем до json.loads: вложенный недоверенный JSON не должен исчерпать стек.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ManifestError("JSON_DEPTH_EXCEEDED", "JSON слишком глубоко вложен. Упростите структуру набора.")
        elif char in "]}":
            depth -= 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("JSON_DUPLICATE_KEY", "В JSON повторяется ключ объекта. Удалите повтор.")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ManifestError("JSON_INVALID", "NaN и Infinity не разрешены в JSON. Исправьте числовые значения.")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_constant(value)
    return number


def _check_unicode(value: object) -> None:
    # ASCII JSON с одиночным \ud800 проходит decode(), но не может стать UTF-8 текстом.
    pending = [value]
    try:
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
    except UnicodeError as exc:
        raise ManifestError("JSON_UNICODE_INVALID", "JSON содержит некорректный Unicode. Исправьте текстовые значения.") from exc


def _parse_manifest(manifest: bytes | str) -> DatasetRegistration:
    try:
        if isinstance(manifest, bytes):
            raw = manifest
        elif isinstance(manifest, str):
            # Ограничиваем до кодирования: число байтов UTF-8 не меньше числа символов.
            if len(manifest) > MAX_MANIFEST_BYTES:
                raise ManifestError("MANIFEST_TOO_LARGE", "Manifest превышает 1 МиБ. Разделите набор.")
            raw = manifest.encode("utf-8")
        else:
            raise ManifestError("MANIFEST_TYPE_INVALID", "Передайте manifest как UTF-8 JSON: байты или строку.")
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ManifestError("MANIFEST_TOO_LARGE", "Manifest превышает 1 МиБ. Разделите набор.")
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ManifestError("MANIFEST_UTF8_INVALID", "Manifest должен быть корректным UTF-8. Пересохраните файл.") from exc

    _check_depth(text)
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float)
    except ManifestError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ManifestError("JSON_INVALID", "JSON не разобран. Проверьте синтаксис manifest.") from exc

    _check_unicode(value)
    if not isinstance(value, dict) or set(value) != {"dataset_id", "synthetic", "tickets"}:
        raise ManifestError("MANIFEST_FIELDS_INVALID", "Нужны только поля dataset_id, synthetic и tickets.")
    if type(value["synthetic"]) is not bool:
        raise ManifestError("SYNTHETIC_FLAG_INVALID", "Укажите synthetic явно: true или false, согласно происхождению данных.", "synthetic")
    tickets = value["tickets"]
    if not isinstance(tickets, list):
        raise ManifestError("TICKETS_INVALID", "Поле tickets должно быть массивом обращений.", "tickets")
    if len(tickets) > MAX_TICKETS:
        raise ManifestError("TICKET_LIMIT_EXCEEDED", "Допустимо не более 100 обращений. Разделите набор.", "tickets")

    sources: set[str] = set()
    asset_ids: set[str] = set()
    has_media = False
    for index, item in enumerate(tickets):
        field = f"tickets.{index}"
        if not isinstance(item, dict) or not isinstance(item.get("created_at"), str):
            raise ManifestError("TICKET_INVALID", "Обращение должно содержать created_at в ISO 8601 с часовым поясом.", field)
        try:
            timestamp = datetime.fromisoformat(item["created_at"])
            if timestamp.utcoffset() is None:
                raise ValueError("Timezone required")
            ticket = TicketInput.model_validate(item)
        except (ValidationError, ValueError) as exc:
            # ValidationError включает input; не выводим ни его, ни значения из обращения.
            raise ManifestError("TICKET_INVALID", "Проверьте поля обращения, дату с часовым поясом и уникальность asset_id.", field) from exc
        if ticket.product_id != "webneft":
            raise ManifestError("PRODUCT_UNSUPPORTED", "В пилоте разрешён только product_id=webneft.", field + ".product_id")
        if ticket.source_id in sources:
            raise ManifestError("SOURCE_ID_DUPLICATE", "source_id внутри набора должны быть уникальны.", field + ".source_id")
        sources.add(ticket.source_id)
        for asset in ticket.assets:
            if asset.asset_id in asset_ids:
                raise ManifestError("ASSET_ID_DUPLICATE", "asset_id должны быть уникальны во всём наборе.", field + ".assets")
            asset_ids.add(asset.asset_id)
            has_media = True
    if has_media:
        raise ManifestError("MEDIA_NOT_SUPPORTED", "В этом этапе поддержан только текст. Уберите вложения; медиаимпорт ещё не подключён.", "tickets")
    try:
        # Оболочка — подмножество готового контракта; собственные DTO не создаём.
        return DatasetRegistration.model_validate(value)
    except ValidationError as exc:
        raise ManifestError("MANIFEST_INVALID", "Проверьте dataset_id: 1–64 латинских буквы, цифры, дефис или подчёркивание.", "dataset_id") from exc


def _invalid(issue: ImportIssue, dataset_id: str | None = None) -> ImportPreview:
    # Проверка останавливается на первой ошибке; counts не являются частичным импортом.
    return ImportPreview(dataset_id=dataset_id, status="invalid", counts=ImportCounts(invalid=1), errors=[issue], can_commit=False)


def import_tickets(
    manifest: bytes | str,
    assets: Collection[object] = (),
    dry_run: bool = False,
    *,
    repository: Repository | None = None,
) -> ImportPreview:
    """Проверить весь текстовый набор. db/counts читаются только в mode=ro.

    repository задаётся сервером, а не полем manifest. Фиксация этим этапом не поддержана.
    """
    if dry_run is not True:
        return _invalid(ImportIssue(code="IMPORT_NOT_IMPLEMENTED", message="Фиксация импорта ещё не подключена. Используйте dry_run=True."))
    if assets:
        return _invalid(ImportIssue(code="MEDIA_NOT_SUPPORTED", message="Переданные файлы пока не поддержаны. Для текстового preview не передавайте assets."))
    try:
        batch = _parse_manifest(manifest)
    except ManifestError as exc:
        return _invalid(exc.issue)

    if repository is None:
        settings = Settings()
        repository = Repository(settings.repo_path(settings.app_db_path))
    try:
        counts = repository.preview_dataset_counts(batch)
    except RepositoryError as exc:
        return _invalid(ImportIssue(code=exc.code, message=exc.message), batch.dataset_id)
    return ImportPreview(
        dataset_id=batch.dataset_id, status="valid", counts=counts, can_commit=True,
        warnings=["Счётчики предварительные. При будущем подтверждении набор проверяется заново."],
    )
