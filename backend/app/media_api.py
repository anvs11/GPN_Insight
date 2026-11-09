"""Точная выдача только synthetic зарегистрированных originals без клиентских путей."""

from hashlib import sha256
import re
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .errors import RepositoryError
from .media_runtime import resolve_registered_asset
from .security import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, checked_open

router = APIRouter(prefix="/api/v1", tags=["synthetic-media"])


def byte_range(header, length):
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()):
        return None
    a, b = match.groups()
    if len(a) > 20 or len(b) > 20:
        return None
    if a:
        start, end = int(a), min(int(b), length - 1) if b else length - 1
    else:
        suffix = int(b)
        if suffix == 0:
            return None
        start, end = max(0, length - suffix), length - 1
    return (start, end) if 0 <= start <= end < length else None


@router.get("/sources/{source_id}/versions/{source_version}/assets/{asset_id}", operation_id="getSyntheticAsset", response_class=Response)
@router.head("/sources/{source_id}/versions/{source_version}/assets/{asset_id}", operation_id="headSyntheticAsset", response_class=Response)
def media(source_id: str, source_version: int, asset_id: str, request: Request,
          product_id: Literal["webneft"] = "webneft"):
    if any(k != "product_id" or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise RepositoryError("UNSUPPORTED_FILTER", "Допустим только однократный product_id; путь/URL не принимаются.", 422)
    if source_version < 1:
        raise RepositoryError("ASSET_CONTEXT_INVALID", "Нужна положительная версия источника.", 422)
    repo, settings = request.app.state.repository, request.app.state.settings
    source = repo.get_source(source_id, product_id, source_version)
    if not source.synthetic:
        raise RepositoryError("ORIGINAL_ACCESS_RESTRICTED", "Оригиналы несинтетических материалов закрыты до согласования доступа.", 403)
    asset = resolve_registered_asset(repo, settings, source_id=source_id, source_version=source_version,
                                     asset_id=asset_id, product_id=product_id)
    limit = MAX_AUDIO_BYTES if asset.mime_type.startswith("audio/") else MAX_IMAGE_BYTES
    try:
        with checked_open(asset.absolute_path, settings.repo_path(settings.app_upload_root)) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit or not raw or sha256(raw).hexdigest() != asset.content_hash:
            raise ValueError
    except (OSError, ValueError):
        raise RepositoryError("REGISTERED_MATERIAL_INTEGRITY_FAILED", "Файл изменён или недоступен. Повторите проверку хранилища.", 503) from None
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": 'inline; filename="synthetic-material.' + {
                   "audio/wav": "wav", "audio/mpeg": "mp3", "image/png": "png", "image/jpeg": "jpg"}[asset.mime_type] + '"',
               "Accept-Ranges": "bytes" if asset.mime_type.startswith("audio/") else "none"}
    status = 200
    # Без выданного validator If-Range не совпадает: возвращаем целый файл.
    if request.method == "GET" and "range" in request.headers and "if-range" not in request.headers:
        interval = byte_range(request.headers["range"], len(raw)) if asset.mime_type.startswith("audio/") else None
        if interval is None:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(raw)}"})
        start, end = interval
        headers["Content-Range"] = f"bytes {start}-{end}/{len(raw)}"
        raw = raw[start:end + 1]
        status = 206
    headers["Content-Length"] = str(len(raw))
    return Response(raw if request.method == "GET" else b"", status_code=status, headers=headers, media_type=asset.mime_type)
