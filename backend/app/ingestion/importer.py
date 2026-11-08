"""Общая проверка manifest/медиа, preview и атомарная регистрация SQLite."""

import json
import math
import sqlite3
from collections.abc import Collection
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from ..config import Settings
from ..repositories import Repository, RepositoryError
from ..schemas import DatasetId, DatasetRegistration, ImportCounts, ImportIssue, ImportPreview, ImportResult, TicketInput
from ..security import MAX_MEDIA, checked_open, validate_filename, validate_upload
from .media_files import ImportWorkspace, StagedUpload, finalize_import_files, open_import_workspace

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


def _parse_manifest(manifest: bytes | str) -> tuple[str, bool, list[TicketInput]]:
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
    parsed_tickets: list[TicketInput] = []
    names: set[str] = set()
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
        parsed_tickets.append(ticket)
        for asset in ticket.assets:
            if asset.asset_id in asset_ids:
                raise ManifestError("ASSET_ID_DUPLICATE", "asset_id должны быть уникальны во всём наборе.", field + ".assets")
            asset_ids.add(asset.asset_id)
            key = validate_filename(asset.path)
            if key in names:
                raise ManifestError("FILE_NAME_DUPLICATE", "Имена вложений должны быть уникальны во всём наборе, включая регистр и Unicode.", field + ".assets")
            names.add(key)
    if len(asset_ids) > MAX_MEDIA:
        raise RepositoryError("MEDIA_FILE_LIMIT_EXCEEDED", "Допустимо не более 30 медиафайлов.", 413)
    try:
        dataset_id = TypeAdapter(DatasetId).validate_python(value["dataset_id"])
    except ValidationError as exc:
        raise ManifestError("MANIFEST_INVALID", "Проверьте dataset_id: 1–64 латинских буквы, цифры, дефис или подчёркивание.", "dataset_id") from exc
    return dataset_id, value["synthetic"], parsed_tickets


def _invalid(issue: ImportIssue, dataset_id: str | None = None) -> ImportPreview:
    # Проверка останавливается на первой ошибке; counts не являются частичным импортом.
    return ImportPreview(dataset_id=dataset_id, status="invalid", counts=ImportCounts(invalid=1), errors=[issue], can_commit=False)


def import_tickets(
    manifest: bytes | str,
    assets: Collection[StagedUpload] = (),
    dry_run: bool = False,
    *,
    repository: Repository | None = None,
    registration_id: str | None = None,
    workspace: ImportWorkspace | None = None,
) -> ImportPreview | ImportResult:
    """Preview читает SQLite; фиксация повторяет проверку и использует общий guard.

    repository и UUID registration_id задаются сервером, не полями manifest.
    Для фиксации нужен обычный Repository без уже открытой внешней транзакции.
    """
    if type(dry_run) is not bool:
        raise RepositoryError("IMPORT_MODE_INVALID", "Параметр dry_run должен быть true или false.", 422)
    try:
        dataset_id, synthetic, tickets = _parse_manifest(manifest)
        requested = [asset for ticket in tickets for asset in ticket.assets]
        if assets and (not isinstance(workspace, ImportWorkspace) or any(not isinstance(a, StagedUpload) for a in assets)):
            raise RepositoryError("MEDIA_WORKSPACE_REQUIRED", "Файлы должен подготовить сервер в текущем workspace.", 422)
        supplied = {a.original_filename: a for a in assets}
        if len(supplied) != len(assets) or {a.path for a in requested} != supplied.keys():
            raise RepositoryError("MEDIA_FILES_MISMATCH", "Для каждого вложения manifest передайте ровно один файл с точным именем, без лишних файлов.", 422)
        if requested and (workspace is None or workspace.dry_run != dry_run):
            raise RepositoryError("MEDIA_WORKSPACE_REQUIRED", "Workspace должен соответствовать режиму импорта.", 422)
        if requested and repository is not None and repository.path.resolve() != workspace.repository.path.resolve():
            raise RepositoryError("MEDIA_WORKSPACE_INVALID", "Workspace должен относиться к текущей базе.", 422)
        if requested:
            workspace.check_storage()
        materials = [validate_upload(supplied[a.path], a, workspace) for a in requested]
        batch = DatasetRegistration(dataset_id=dataset_id, synthetic=synthetic, tickets=tickets, materials=materials)
    except ManifestError as exc:
        if dry_run:
            return _invalid(exc.issue)
        raise RepositoryError(exc.issue.code, exc.issue.message, 422) from None
    except RepositoryError as exc:
        if dry_run and exc.status_code == 422:
            return _invalid(ImportIssue(code=exc.code, message=exc.message))
        raise

    if repository is None:
        settings = Settings()
        repository = Repository(settings.repo_path(settings.app_db_path))
    if dry_run:
        try:
            counts = repository.preview_dataset_counts(batch)
        except RepositoryError as exc:
            return _invalid(ImportIssue(code=exc.code, message=exc.message), batch.dataset_id)
        return ImportPreview(
            dataset_id=batch.dataset_id, status="valid", counts=counts, can_commit=True,
            warnings=["Счётчики предварительные. При будущем подтверждении набор проверяется заново."],
        )

    # Сервис не может подтвердить commit транзакции, которой управляет его вызывающий код.
    if repository._db is not None:
        raise RepositoryError(
            "IMPORT_TRANSACTION_ACTIVE",
            "Вызовите импорт с обычным Repository до открытия внешней DB-транзакции.",
            409,
        )
    try:
        request_uuid = str(uuid4()) if registration_id is None else str(UUID(registration_id))
    except (ValueError, TypeError, AttributeError):
        raise RepositoryError("REGISTRATION_ID_INVALID", "ID запроса регистрации должен быть серверным UUID.", 422) from None

    # Не создаём/мигрируем отсутствующую БД при фиксации. Это лишь проверка состояния:
    # её counts не используются как итог и снимок чтения закрыт до ожидания guard.
    repository.preview_dataset_counts(batch)
    try:
        with repository.import_commit():
            result = (finalize_import_files(workspace, batch, repository, request_uuid) if batch.materials
                      else repository.register_dataset(batch, registration_id=request_uuid))
    except sqlite3.Error:
        raise RepositoryError(
            "IMPORT_DATABASE_UNAVAILABLE",
            "Импорт не подтверждён из-за ошибки базы. Проверьте её состояние и повторите тот же набор.",
            503,
        ) from None
    except OSError:
        raise RepositoryError("IMPORT_STORAGE_UNAVAILABLE", "Файлы не подтверждены. Проверьте доступ к хранилищу и результат импорта; повтор набора безопасен.", 503) from None
    # register_dataset завершил commit; выход из guard не является DB-транзакцией.
    return result


def import_from_directory(manifest: bytes | str, directory, *, settings: Settings,
                          repository: Repository, dry_run: bool) -> ImportPreview | ImportResult:
    """CLI выбирает только перечисленные файлы; общий importer повторно проверяет всё."""
    try:
        _, _, tickets = _parse_manifest(manifest)
        requested = [asset for ticket in tickets for asset in ticket.assets]
        if not requested:
            return import_tickets(manifest, dry_run=dry_run, repository=repository)
        from .media_files import reconcile_import_storage, save_recovery_report
        recovery = reconcile_import_storage(repository, settings, read_only=dry_run)
        if not dry_run:
            save_recovery_report(recovery)
        if recovery.issues:
            raise RepositoryError(recovery.issues[0], "Нужна проверка файлового хранения. Перезапустите сервер для восстановления и повторите набор.", 503)
        with open_import_workspace(settings, repository, dry_run=dry_run) as workspace:
            for asset in requested:
                with checked_open(directory / asset.path, directory) as source:
                    workspace.stage_stream(asset.path, source)
            return import_tickets(manifest, assets=workspace.uploads, dry_run=dry_run,
                                  repository=repository, workspace=workspace)
    except ManifestError:
        return import_tickets(manifest, dry_run=dry_run, repository=repository)
    except RepositoryError as exc:
        if dry_run and exc.status_code == 422:
            return _invalid(ImportIssue(code=exc.code, message=exc.message))
        raise
