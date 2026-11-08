"""Владение временными файлами и восстановление; SQLite остаётся в Repository."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import BinaryIO
from uuid import uuid4

from ..config import REPO_ROOT, Settings
from ..errors import RepositoryError
from ..schemas import DatasetRegistration, ImportResult
from ..security import (
    CHUNK_BYTES, MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_AUDIO_SECONDS, MAX_IMAGE_PIXELS, MAX_MEDIA,
    MAX_MEDIA_BYTES, checked_open, checked_path, file_digest, invalid, validate_filename,
)

_UUID = re.compile(r"^[0-9a-f]{32}$")
_FINAL = re.compile(r"^requests/([0-9a-f]{32})/[0-9a-f]{32}\.(wav|mp3|png|jpg)$")
_TEMP = re.compile(r"^(body\.bin|[0-9a-f]{32}\.bin|journal\.json|journal\.next)$")
_OWNER = ".import-owner.json"


def _root(settings: Settings, value: Path) -> Path:
    path = Path(os.path.abspath(value if value.is_absolute() else REPO_ROOT / value))
    return checked_path(path, Path(path.anchor))


def _owner(repository) -> str:
    return sha256(str(repository.path.resolve()).encode("utf-8")).hexdigest()


def _sync_directory(path: Path) -> None:
    # Windows не предоставляет POSIX directory fsync. Обещаем process-stop recovery.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _mkdir(path: Path) -> list[Path]:
    checked_path(path, Path(path.anchor))
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    return missing


def _read_json(path: Path, root: Path, limit: int = 65536):
    with checked_open(path, root) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Oversized server journal")
    try:
        return json.loads(data)
    except RecursionError:
        raise ValueError("Server journal nesting is invalid") from None


def _write_json(path: Path, value) -> None:
    temporary = path.with_name("journal.next" if path.name == "journal.json" else uuid4().hex + ".bin")
    with temporary.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)  # Только собственный служебный JSON, не материалы.
    _sync_directory(path.parent)


def _storage_owner(root: Path, owner: str, *, create: bool) -> None:
    checked_path(root, Path(root.anchor))
    if not root.exists():
        if not create:
            return
        _mkdir(root)
    marker = root / _OWNER
    if marker.exists() or marker.is_symlink():
        try:
            stored = _read_json(marker, root)
        except (OSError, ValueError, RepositoryError):
            raise invalid("IMPORT_STORAGE_OWNER_INVALID", "Маркер владельца файлов повреждён или недоступен. Проверьте хранилище; файлы сохранены.", 503) from None
        if stored != {"schema": 1, "owner": owner}:
            raise invalid("IMPORT_STORAGE_OWNER_CONFLICT", "Каталог вложений относится к другой базе. Проверьте настройки хранения.", 503)
    elif any(root.iterdir()):
        raise invalid("IMPORT_STORAGE_OWNER_UNCONFIRMED", "Непустой каталог вложений не имеет подтверждённого владельца. Нужна проверка хранения.", 503)
    elif create:
        _write_json(marker, {"schema": 1, "owner": owner})


def _registry(repository, root: Path, product_id: str | None = None):
    # Не создавать отсутствующую БД даже при standalone recovery.
    repository.preview_dataset_counts(DatasetRegistration(dataset_id="storage-check", synthetic=True, tickets=[]))
    try:
        values = repository.registered_materials() if product_id is None else repository.registered_materials(product_id)
    except (sqlite3.Error, ValueError):
        raise invalid("IMPORT_STORAGE_UNAVAILABLE", "Не удалось прочитать полный реестр файлов. Ничего не удалено; проверьте базу.", 503) from None
    for material in values:
        checked_path(root / material.storage_path, root)
    return values


def _verify_registered(material, root: Path) -> None:
    path = checked_path(root / material.storage_path, root)
    try:
        if not path.is_file() or path.stat().st_size != material.byte_size:
            raise ValueError
        if file_digest(path, root) != (material.content_hash, material.byte_size):
            raise ValueError
    except (ValueError, OSError, RepositoryError):
        raise invalid("REGISTERED_MATERIAL_INTEGRITY_FAILED", "Зарегистрированный материал отсутствует или повреждён. Проверьте файловое хранилище; прежние записи сохранены.", 503) from None


@dataclass(frozen=True)
class StagedUpload:
    original_filename: str
    temporary_path: Path


@dataclass
class RecoveryReport:
    issues: list[str] = field(default_factory=list)
    preserved: int = 0
    removed: int = 0
    materials: list[dict[str, str]] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        return "issues" if self.issues else "ok"


class ImportWorkspace:
    def __init__(self, settings: Settings, repository, *, dry_run: bool):
        self.repository = repository
        self.owner = _owner(repository)
        self.upload_root = _root(settings, settings.app_upload_root)
        self.tmp_root = _root(settings, settings.app_tmp_root)
        if (self.upload_root == self.tmp_root or self.upload_root.is_relative_to(self.tmp_root)
                or self.tmp_root.is_relative_to(self.upload_root)):
            raise invalid("IMPORT_STORAGE_CONFIG_INVALID", "Временный каталог и каталог вложений должны быть раздельными.", 503)
        self.max_audio_seconds = settings.app_max_audio_seconds
        self.max_image_pixels = settings.app_max_image_pixels
        if not (0 < self.max_audio_seconds <= MAX_AUDIO_SECONDS and 0 < self.max_image_pixels <= MAX_IMAGE_PIXELS):
            raise invalid("IMPORT_STORAGE_CONFIG_INVALID", "Лимиты аудио/изображений должны быть положительными и не превышать 60 секунд/25 Мп.", 503)
        self.request_id = uuid4().hex
        self.folder = self.tmp_root / "imports" / self.request_id
        self.created_parents = _mkdir(self.folder)
        self.dry_run, self.retain = dry_run, False
        self.uploads: list[StagedUpload] = []
        self.handles: list[BinaryIO] = []
        self.names: set[str] = set()
        self.total_bytes = 0
        self.sizes: dict[Path, int] = {}
        self.file_limits: dict[Path, int] = {}
        self.fingerprints: dict[Path, tuple] = {}
        self.planned: list[dict] = []
        self.directory_identity: list[int] | None = None
        try:
            ancestor = self.upload_root
            while not ancestor.exists():
                ancestor = ancestor.parent
            if ancestor.stat().st_dev != self.folder.stat().st_dev:
                raise invalid("IMPORT_STORAGE_DEVICE_MISMATCH", "Tmp и uploads должны находиться на одном диске.", 503)
            self.journal()
        except BaseException:
            self.close()
            raise

    def journal(self) -> None:
        _write_json(self.folder / "journal.json", {"schema": 1, "request_id": self.request_id,
            "owner": self.owner, "files": self.planned, "directory_identity": self.directory_identity})

    def raw_body(self) -> BinaryIO:
        stream = (self.folder / "body.bin").open("xb+")
        self.handles.append(stream)
        return stream

    def begin_upload(self, filename: str) -> tuple[StagedUpload, BinaryIO]:
        key = validate_filename(filename)
        if key in self.names:
            raise invalid("FILE_NAME_DUPLICATE", "Имена файлов не должны совпадать, в том числе без учёта регистра и Unicode.")
        if len(self.uploads) >= MAX_MEDIA:
            raise invalid("MEDIA_FILE_LIMIT_EXCEEDED", "Допустимо не более 30 медиафайлов.", 413)
        self.names.add(key)
        upload = StagedUpload(filename, self.folder / (uuid4().hex + ".bin"))
        stream = upload.temporary_path.open("xb")
        self.handles.append(stream)
        self.uploads.append(upload)
        self.sizes[upload.temporary_path] = 0
        self.file_limits[upload.temporary_path] = (MAX_IMAGE_BYTES if Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg"} else MAX_AUDIO_BYTES)
        return upload, stream

    def write_upload(self, upload: StagedUpload, stream: BinaryIO, data: bytes) -> None:
        self.check_upload(upload)
        size = self.sizes[upload.temporary_path] + len(data)
        if size > self.file_limits[upload.temporary_path] or self.total_bytes + len(data) > MAX_MEDIA_BYTES:
            raise invalid("MEDIA_FILE_TOO_LARGE", "Превышен предел 10 МиБ на аудио, 5 МиБ на изображение или 50 МиБ на все вложения.", 413)
        stream.write(data)
        self.sizes[upload.temporary_path] = size
        self.total_bytes += len(data)

    def finish_upload(self, stream: BinaryIO) -> None:
        stream.flush()
        os.fsync(stream.fileno())
        self.fingerprints[Path(stream.name)] = self._fingerprint(os.fstat(stream.fileno()))
        stream.close()

    def stage_stream(self, filename: str, source: BinaryIO) -> StagedUpload:
        upload, stream = self.begin_upload(filename)
        while chunk := source.read(CHUNK_BYTES):
            self.write_upload(upload, stream, chunk)
        self.finish_upload(stream)
        return upload

    @staticmethod
    def _fingerprint(info) -> tuple:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink

    def check_upload(self, upload: StagedUpload, *, complete: bool = False) -> None:
        if not any(upload is value for value in self.uploads):
            raise invalid("MEDIA_WORKSPACE_INVALID", "Передайте файлы, подготовленные текущим серверным workspace.")
        checked_path(upload.temporary_path, self.folder)
        if complete and (upload.temporary_path not in self.fingerprints
                         or self._fingerprint(upload.temporary_path.lstat()) != self.fingerprints[upload.temporary_path]):
            raise invalid("FILE_CHANGED", "Подготовленный файл изменился. Повторите загрузку.")

    def storage_path(self, upload: StagedUpload, mime: str) -> str:
        extension = {"audio/wav": "wav", "audio/mpeg": "mp3", "image/png": "png", "image/jpeg": "jpg"}[mime]
        return f"requests/{self.request_id}/{upload.temporary_path.stem}.{extension}"

    def check_storage(self) -> None:
        _storage_owner(self.upload_root, self.owner, create=False)

    def close(self) -> None:
        for stream in self.handles:
            if not stream.closed:
                stream.close()
        if self.retain:
            return
        _remove_temporary(self.folder)
        for parent in self.created_parents:
            if parent != self.folder:
                try:
                    parent.rmdir()
                except OSError:
                    pass


@contextmanager
def open_import_workspace(settings: Settings, repository, *, dry_run: bool):
    try:
        workspace = ImportWorkspace(settings, repository, dry_run=dry_run)
    except OSError:
        raise invalid("IMPORT_STORAGE_UNAVAILABLE", "Не удалось создать временный каталог. Проверьте доступ и свободное место.", 503) from None
    try:
        yield workspace
    except OSError:
        raise invalid("IMPORT_STORAGE_UNAVAILABLE", "Не удалось подготовить файлы. Проверьте место на диске и доступ; повторите набор.", 503) from None
    finally:
        try:
            workspace.close()
        except (OSError, RepositoryError):
            workspace.retain = True
            raise invalid("IMPORT_CLEANUP_PENDING", "Очистка временных файлов отложена. Проверьте результат импорта и хранилище; повтор набора безопасен.", 503) from None


def _temporary_entries(folder: Path) -> list[Path]:
    checked_path(folder, folder.parent)
    if not folder.exists():
        return []
    entries = list(folder.iterdir())
    for path in entries:
        if not _TEMP.fullmatch(path.name):
            raise invalid("IMPORT_JOURNAL_INVALID", "Неизвестные временные файлы сохранены для проверки.", 503)
        with checked_open(path, folder):
            pass
    return entries


def _remove_temporary(folder: Path) -> None:
    entries = _temporary_entries(folder)
    if not folder.exists():
        return
    for path in sorted(entries, key=lambda p: p.name == "journal.json"):
        path.unlink()
    folder.rmdir()


def _cleanup_final(repository, workspace) -> None:
    registered = {m.storage_path for m in _registry(repository, workspace.upload_root)}
    directory = checked_path(workspace.upload_root / "requests" / workspace.request_id, workspace.upload_root)
    if directory.exists():
        info = directory.lstat()
        if workspace.directory_identity != [info.st_dev, info.st_ino]:
            raise invalid("IMPORT_JOURNAL_INVALID", "Принадлежность каталога запроса не подтверждена; файлы сохранены для проверки.", 503)
    elif workspace.directory_identity is None:
        return
    for item in workspace.planned:
        target = checked_path(workspace.upload_root / item["storage_path"], workspace.upload_root)
        staged = workspace.folder / (target.stem + ".bin")
        # Process-stop между link(EXCL) и unlink оставляет две собственные ссылки.
        if staged.exists() and target.exists():
            checked_path(staged, workspace.folder)
            left, right = staged.lstat(), target.lstat()
            if (left.st_dev, left.st_ino, left.st_nlink) == (right.st_dev, right.st_ino, 2):
                staged.unlink()
        if item["storage_path"] not in registered:
            if target.exists():
                if file_digest(target, workspace.upload_root) != (item["content_hash"], item["byte_size"]):
                    raise invalid("IMPORT_JOURNAL_INVALID", "Изменённый незарегистрированный файл сохранён для проверки.", 503)
                target.unlink()
    if directory.exists():
        try:
            directory.rmdir()
        except OSError:
            pass


def finalize_import_files(workspace: ImportWorkspace, batch: DatasetRegistration, repository, registration_id: str) -> ImportResult:
    if workspace.dry_run or workspace.owner != _owner(repository):
        raise invalid("MEDIA_WORKSPACE_INVALID", "Workspace не соответствует базе или режиму импорта.")
    with repository.import_commit():
        _storage_owner(workspace.upload_root, workspace.owner, create=True)
        _registry(repository, workspace.upload_root)  # Полная метадата до любых final-файлов.
        registered = _registry(repository, workspace.upload_root, batch.product_id)
        requested = {(m.asset_id, m.content_hash) for m in batch.materials}
        for material in registered:
            if (material.asset_id, material.content_hash) in requested:
                _verify_registered(material, workspace.upload_root)
        workspace.planned = [m.model_dump(mode="json", include={"storage_path", "content_hash", "byte_size"}) for m in batch.materials]
        workspace.journal()
        workspace.retain = True
        try:
            destination = workspace.upload_root / "requests" / workspace.request_id
            _mkdir(destination.parent)
            destination.mkdir(mode=0o700)
            info = destination.lstat()
            workspace.directory_identity = [info.st_dev, info.st_ino]
            _sync_directory(destination.parent)
            workspace.journal()  # Доказательство EXCL-создания до первого файла.
            by_name = {upload.original_filename: upload for upload in workspace.uploads}
            for material in batch.materials:
                upload = by_name[material.original_filename]
                workspace.check_upload(upload, complete=True)
                # EXCL-семантика hard-link исключает перезапись; затем переносим владение.
                target = checked_path(workspace.upload_root / material.storage_path, workspace.upload_root)
                os.link(upload.temporary_path, target, follow_symlinks=False)
                upload.temporary_path.unlink()
                if file_digest(target, workspace.upload_root) != (material.content_hash, material.byte_size):
                    raise invalid("FILE_CHANGED", "Файл изменился до регистрации. Повторите загрузку; набор не подтверждён.")
            _sync_directory(destination)
            result = repository.register_dataset(batch, registration_id=registration_id)
        finally:
            try:
                _cleanup_final(repository, workspace)
                workspace.retain = False
            except (OSError, RepositoryError):
                # Commit мог пройти. При неопределённости журнал и final сохраняются.
                workspace.retain = True
        if workspace.retain:
            raise invalid("IMPORT_CLEANUP_PENDING", "Импорт сохранён, но очистка временных файлов отложена. Проверьте хранилище; повтор набора безопасен.", 503)
        return result


def reconcile_import_storage(repository, settings: Settings, *, read_only: bool = False) -> RecoveryReport:
    report = RecoveryReport()
    try:
        uploads = _root(settings, settings.app_upload_root)
        temporary = _root(settings, settings.app_tmp_root) / "imports"
        _storage_owner(uploads, _owner(repository), create=False)
        registered = _registry(repository, uploads)
        for material in registered:
            try:
                _verify_registered(material, uploads)
                report.preserved += 1
            except RepositoryError as exc:
                report.issues.append(exc.code)
                report.materials.append({"asset_id": material.asset_id, "code": exc.code})
        if read_only or not temporary.exists():
            return report
        with repository.import_commit():
            for folder in temporary.iterdir():
                try:
                    if not _UUID.fullmatch(folder.name):
                        raise ValueError
                    checked_path(folder, temporary)
                    journal = _read_json(folder / "journal.json", folder)
                    if (set(journal) != {"schema", "request_id", "owner", "files", "directory_identity"}
                            or journal["schema"] != 1 or journal["request_id"] != folder.name
                            or journal["owner"] != _owner(repository) or not isinstance(journal["files"], list)
                            or len(journal["files"]) > MAX_MEDIA):
                        raise ValueError
                    identity = journal["directory_identity"]
                    if identity is not None and (not isinstance(identity, list) or len(identity) != 2
                                               or any(type(v) is not int or v < 0 for v in identity)):
                        raise ValueError
                    for item in journal["files"]:
                        match = _FINAL.fullmatch(item["storage_path"])
                        if (not match or match[1] != folder.name or set(item) != {"storage_path", "content_hash", "byte_size"}
                                or not re.fullmatch(r"[0-9a-f]{64}", item["content_hash"])
                                or type(item["byte_size"]) is not int or not 0 < item["byte_size"] <= MAX_AUDIO_BYTES):
                            raise ValueError
                    # Простой объект для общего cleanup, без создания нового workspace.
                    # Сначала исключаем неизвестные/подменённые временные записи.
                    # Допустимую собственную link-window исправляет общий cleanup.
                    for entry in folder.iterdir():
                        if not _TEMP.fullmatch(entry.name):
                            raise ValueError
                        checked_path(entry, folder)
                    from types import SimpleNamespace
                    state = SimpleNamespace(upload_root=uploads, planned=journal["files"], request_id=folder.name,
                                            folder=folder, directory_identity=identity)
                    _cleanup_final(repository, state)
                    _remove_temporary(folder)
                    report.removed += 1
                except (OSError, ValueError, KeyError, TypeError, RepositoryError):
                    report.issues.append("IMPORT_JOURNAL_INVALID")
    except (OSError, ValueError, TypeError, RepositoryError) as exc:
        report.issues.append(exc.code if isinstance(exc, RepositoryError) else "IMPORT_STORAGE_UNAVAILABLE")
    report.issues = sorted(set(report.issues))
    return report


def save_recovery_report(report: RecoveryReport) -> None:
    if report.issues or report.removed:
        folder = REPO_ROOT / "var" / "reports"
        _mkdir(folder)
        _write_json(folder / ("import-recovery-" + uuid4().hex + ".json"), asdict(report))
