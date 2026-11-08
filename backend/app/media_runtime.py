"""Read-only подготовка постоянного файла к будущему ASR/VL; не распознавание."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import os
import sqlite3

from .config import Settings
from .errors import RepositoryError
from .gateways import ApprovedAsset
from .ingestion.media_files import _FINAL, _owner, _root, _storage_owner
from .repositories import Repository
from .schemas import DatasetRegistration
from .security import CHUNK_BYTES, MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, checked_open, checked_path
from .storage import source_content_hash


def resolve_registered_asset(
    repository: Repository,
    settings: Settings,
    *,
    source_id: str,
    source_version: int,
    asset_id: str,
    product_id: str = "webneft",
) -> ApprovedAsset:
    """Проверяет точную версию; не использует staged path/имя/URL из запроса.

    Возвращённый путь не является открытым файловым дескриптором. Будущий gateway
    обязан безопасно переоткрыть файл и сверить hash непосредственно перед отправкой.
    """
    if (type(source_version) is not int or source_version < 1
            or any(not isinstance(value, str) or not value or len(value) > 128
                   or any(c.isspace() for c in value) for value in (product_id, source_id, asset_id))):
        raise RepositoryError("ASSET_CONTEXT_INVALID", "Укажите продукт, источник, точную целую версию и идентификатор материала.", 422)

    # Проверяет наличие/схему/rollback journal без создания SQLite/WAL/SHM.
    repository.preview_dataset_counts(DatasetRegistration(dataset_id="asset-read-check", synthetic=True, tickets=[]))
    try:
        with closing(sqlite3.connect(repository.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only = ON")
            db.execute("BEGIN")
            snapshot = Repository(repository.path, _db=db)
            source = snapshot.get_source(source_id, product_id, source_version)
            materials = snapshot.source_materials(source_id, product_id, source_version)
        by_id = {material.asset_id: material for material in materials}
        expected = {asset.asset_id: asset for asset in source.ticket.assets}
        if (len(by_id) != len(materials) or by_id.keys() != expected.keys()
                or source_content_hash(source.ticket, by_id) != source.content_hash
                or any((by_id[key].kind, by_id[key].mime_type) != (value.kind, value.mime_type)
                       for key, value in expected.items())):
            raise RepositoryError("ASSET_REGISTRY_CONFLICT", "Материалы не соответствуют сохранённой версии источника. Проверьте реестр; данные сохранены.", 503)
    except (sqlite3.Error, ValueError, OSError):
        raise RepositoryError("ASSET_REGISTRY_UNAVAILABLE", "Не удалось прочитать согласованный реестр материала. Проверьте базу и повторите.", 503) from None
    if asset_id not in expected:
        raise RepositoryError("SOURCE_ASSET_NOT_FOUND", "Материал не принадлежит указанной версии источника.", 404)

    material = by_id[asset_id]
    root = _root(settings, settings.app_upload_root)
    # Используем тот же owner/protocol постоянного хранилища, что импорт/recovery U1.
    _storage_owner(root, _owner(repository), create=False)
    suffix = {"audio/wav": ".wav", "audio/mpeg": ".mp3", "image/png": ".png", "image/jpeg": ".jpg"}[material.mime_type]
    limit = MAX_AUDIO_BYTES if material.kind == "audio" else MAX_IMAGE_BYTES
    if (not _FINAL.fullmatch(material.storage_path) or Path(material.storage_path).suffix != suffix
            or material.byte_size > limit):
        raise RepositoryError("ASSET_STORAGE_INVALID", "Материал не имеет допустимого постоянного пути или размера. Проверьте хранилище.", 503)
    path = checked_path(root / material.storage_path, root)
    digest, count = sha256(), 0
    try:
        with checked_open(path, root) as stream:
            # Ограничиваем чтение даже при подмене/увеличении файла после регистрации.
            if os.fstat(stream.fileno()).st_size != material.byte_size:
                raise ValueError
            while chunk := stream.read(min(CHUNK_BYTES, material.byte_size + 1 - count)):
                count += len(chunk)
                digest.update(chunk)
                if count > material.byte_size:
                    raise ValueError
            if count != material.byte_size or digest.hexdigest() != material.content_hash:
                raise ValueError
    except (ValueError, OSError):
        raise RepositoryError("REGISTERED_MATERIAL_INTEGRITY_FAILED", "Зарегистрированный файл изменён или недоступен. Проверьте хранилище; данные сохранены.", 503) from None
    return ApprovedAsset(material.asset_id, path, material.content_hash, material.mime_type)
