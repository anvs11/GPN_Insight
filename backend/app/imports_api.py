"""Ограниченный multipart-конверт; правила manifest/регистрации остаются importer."""

import logging

from fastapi import APIRouter, Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .ingestion.importer import MAX_MANIFEST_BYTES, import_tickets
from .repositories import RepositoryError
from .schemas import ErrorResponse, ImportCounts, ImportIssue, ImportLogEntry, ImportPreview, ImportResult

MAX_BODY_BYTES = MAX_MANIFEST_BYTES + 64 * 1024
MAX_PART_BYTES = MAX_MANIFEST_BYTES
MAX_PARTS = MAX_FIELDS = MAX_FILES = 8
MAX_PART_HEADER_BYTES = 8 * 1024
READ_CHUNK_BYTES = 32 * 1024
MAX_BOUNDARY_BYTES = 70
_PARSER_LOGGER = logging.Logger("gpn.imports.multipart", level=logging.CRITICAL + 1)

router = APIRouter(prefix="/api/v1", tags=["imports"], responses={
    code: {"model": ErrorResponse} for code in (403, 409, 413, 422, 503)
})


class ImportRequestError(RepositoryError):
    pass


class _ManifestParts:
    def __init__(self):
        self.parts = self.fields = self.files = self.manifests = 0
        self.manifest = bytearray()
        self.issue: ImportRequestError | None = None
        self.ended = False

    def invalid(self, code, message):
        if self.issue is None:
            self.issue = ImportRequestError(code, message, 422)

    def on_part_begin(self):
        self.parts += 1
        if self.parts > MAX_PARTS:
            raise ImportRequestError("IMPORT_PART_LIMIT_EXCEEDED", "Слишком много частей multipart. Допустимо не более 8.", 413)
        self.headers = {}
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.header_bytes = self.part_bytes = 0
        self.collect_manifest = False

    def _header(self, destination, data, start, end):
        self.header_bytes += end - start
        if self.header_bytes > MAX_PART_HEADER_BYTES:
            raise ImportRequestError("IMPORT_HEADERS_TOO_LARGE", "Заголовки части превышают 8 КиБ.", 413)
        destination.extend(data[start:end])

    def on_header_field(self, data, start, end):
        self._header(self.header_name, data, start, end)

    def on_header_value(self, data, start, end):
        self._header(self.header_value, data, start, end)

    def on_header_end(self):
        self.header_bytes += 4  # двоеточие/пробел/CRLF тоже входят в лимит
        if self.header_bytes > MAX_PART_HEADER_BYTES:
            raise ImportRequestError("IMPORT_HEADERS_TOO_LARGE", "Заголовки части превышают 8 КиБ.", 413)
        name = bytes(self.header_name).lower()
        if name in self.headers:
            self.invalid("MULTIPART_INVALID", "Заголовки части не должны повторяться.")
        self.headers[name] = bytes(self.header_value)
        self.header_name.clear()
        self.header_value.clear()

    def on_headers_finished(self):
        kind, options = parse_options_header(self.headers.get(b"content-disposition", b""))
        if kind.lower() != b"form-data" or b"name" not in options:
            self.invalid("MULTIPART_INVALID", "Для каждой части нужны form-data и имя поля.")
        if b"filename" in options:
            self.files += 1
            if self.files > MAX_FILES:
                raise ImportRequestError("IMPORT_FILE_LIMIT_EXCEEDED", "Слишком много файлов multipart.", 413)
        else:
            self.fields += 1
            if self.fields > MAX_FIELDS:
                raise ImportRequestError("IMPORT_FIELD_LIMIT_EXCEEDED", "Слишком много полей multipart.", 413)
        name = options.get(b"name")
        if name == b"manifest":
            self.manifests += 1
            if self.manifests > 1:
                self.invalid("MANIFEST_DUPLICATE", "Передайте ровно один manifest.")
            else:
                self.collect_manifest = True
        elif name == b"files":
            self.invalid("MEDIA_NOT_SUPPORTED", "Текстовый импорт не принимает files. Уберите вложения целиком.")
        else:
            self.invalid("MULTIPART_FIELD_UNSUPPORTED", "Разрешены только manifest и files; files пока не поддерживаются.")

    def on_part_data(self, data, start, end):
        self.part_bytes += end - start
        if self.collect_manifest and len(self.manifest) + end - start > MAX_MANIFEST_BYTES:
            raise ImportRequestError("MANIFEST_TOO_LARGE", "Manifest превышает 1 МиБ.", 413)
        if self.part_bytes > MAX_PART_BYTES:
            raise ImportRequestError("IMPORT_PART_TOO_LARGE", "Часть multipart превышает 1 МиБ.", 413)
        if self.collect_manifest:
            self.manifest.extend(data[start:end])

    def on_end(self):
        self.ended = True


async def _read_body(request: Request) -> bytearray:
    # Никакого request.form()/UploadFile: Starlette может начать spooling до
    # проверки лимита файла. Сначала считаем фактические байты всего конверта.
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise ImportRequestError("IMPORT_BODY_TOO_LARGE", "Тело запроса превышает 1 МиБ + 64 КиБ.", 413)
            body.extend(chunk)
    except ClientDisconnect:
        raise ImportRequestError("IMPORT_BODY_INCOMPLETE", "Загрузка прервана. Отправьте полный набор заново.", 422) from None
    return body


def _manifest_from_body(request: Request, body: bytearray) -> bytes:
    types = request.headers.getlist("content-type")
    if len(types) != 1 or request.headers.get("content-encoding", "identity").lower() != "identity":
        raise ImportRequestError("MULTIPART_INVALID", "Передайте несжатый multipart/form-data с одним manifest.", 422)
    try:
        kind, options = parse_options_header(types[0])
    except (TypeError, ValueError):
        raise ImportRequestError("MULTIPART_INVALID", "Проверьте Content-Type multipart/form-data.", 422) from None
    boundary = options.get(b"boundary", b"")
    if (kind.lower() != b"multipart/form-data" or not 1 <= len(boundary) <= MAX_BOUNDARY_BYTES or
        any(byte < 32 or byte > 126 for byte in boundary) or boundary.endswith(b" ")):
        raise ImportRequestError("MULTIPART_INVALID", "Проверьте Content-Type и boundary multipart/form-data.", 422)
    parts = _ManifestParts()
    callbacks = {name: getattr(parts, name) for name in (
        "on_part_begin", "on_header_field", "on_header_value", "on_header_end",
        "on_headers_finished", "on_part_data", "on_end")}
    # Собственные callback-лимиты дают единый HTTP 413 вместо версии-зависимых
    # parse errors библиотеки; весь body уже ограничен до создания парсера.
    parser = MultipartParser(boundary, callbacks, max_header_count=MAX_PART_HEADER_BYTES, max_header_size=MAX_BODY_BYTES)
    # Parser warnings могут содержать байты недоверенного ввода. Подавляем их
    # только для этого parser; HTTP возвращает наш безопасный код/сообщение.
    parser.logger = _PARSER_LOGGER
    try:
        for start in range(0, len(body), READ_CHUNK_BYTES):
            parser.write(bytes(body[start:start + READ_CHUNK_BYTES]))
        parser.finalize()
    except (MultipartParseError, ValueError):
        raise ImportRequestError("MULTIPART_INVALID", "Multipart не разобран. Отправьте полный корректный конверт.", 422) from None
    # finalize() сам не гарантирует закрывающую границу. Без on_end commit запрещён.
    if not parts.ended:
        raise ImportRequestError("MULTIPART_INCOMPLETE", "Нет завершающей границы multipart. Отправьте запрос заново.", 422)
    if parts.issue:
        raise parts.issue
    if parts.manifests != 1:
        raise ImportRequestError("MANIFEST_MISSING", "Передайте ровно один manifest.", 422)
    return bytes(parts.manifest)


def _check_query(request: Request, *, post: bool):
    values = list(request.query_params.multi_items())
    if (not post and values) or (post and (len(values) > 1 or any(
        name != "dry_run" or value not in {"true", "false"} for name, value in values))):
        message = ("Допустим только один dry_run=true или dry_run=false для POST." if post else
                   "GET /imports не принимает query-параметров. Уберите их из запроса.")
        raise ImportRequestError("IMPORT_QUERY_INVALID", message, 422)


@router.post("/imports", response_model=ImportPreview | ImportResult, openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["manifest"], "properties": {
            "manifest": {"type": "string", "format": "binary", "description": "Один UTF-8 JSON, до 1 МиБ"},
            "files": {"type": "array", "items": {"type": "string", "format": "binary"},
                      "description": "Повторяющиеся files; в текстовом этапе отклоняются целиком"}}}}}}
})
async def post_import(request: Request, dry_run: bool = False):
    _check_query(request, post=True)
    try:
        body = await _read_body(request)
        manifest = _manifest_from_body(request, body)
    except ImportRequestError as exc:
        if not dry_run or exc.status_code == 413:
            raise
        return ImportPreview(status="invalid", counts=ImportCounts(invalid=1), can_commit=False,
            errors=[ImportIssue(code=exc.code, message=exc.message)])
    return await run_in_threadpool(import_tickets, manifest, dry_run=dry_run, repository=request.app.state.repository)


@router.get("/imports", response_model=list[ImportLogEntry])
async def get_imports(request: Request):
    _check_query(request, post=False)
    return await run_in_threadpool(request.app.state.repository.list_imports)
