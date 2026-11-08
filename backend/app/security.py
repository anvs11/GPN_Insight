"""Общие проверки файлов набора; недоверенные имена не становятся путями хранения."""

from contextlib import contextmanager
from hashlib import sha256
import math
import os
from pathlib import Path
import stat
import unicodedata
import warnings
import wave

from .errors import RepositoryError
from .gateways import ApprovedAsset
from .schemas import MaterialInput, RegisteredMaterial

MIB = 1024 * 1024
MAX_MEDIA = 30
MAX_MEDIA_BYTES = 50 * MIB
MAX_BODY_BYTES = 52 * MIB
MAX_AUDIO_BYTES = 10 * MIB
MAX_IMAGE_BYTES = 5 * MIB
MAX_AUDIO_SECONDS = 60
MAX_IMAGE_PIXELS = 25_000_000
CHUNK_BYTES = 32 * 1024
EXTENSIONS = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    prefix + digit for prefix in ("com", "lpt") for digit in "123456789¹²³"
}


def invalid(code: str, message: str, status: int = 422) -> RepositoryError:
    return RepositoryError(code, message, status)


def validate_filename(name: str) -> str:
    if (not isinstance(name, str) or not name or len(name) > 255
            or unicodedata.normalize("NFC", name) != name
            or name.startswith(".") or name.endswith((" ", "."))
            or any(c in name for c in '/\\:<>"|?*')
            or any(unicodedata.category(c).startswith("C") for c in name)
            or name.split(".", 1)[0].rstrip(" .").casefold() in _RESERVED):
        raise invalid("FILE_NAME_INVALID", "Используйте простое безопасное имя файла без каталогов, специальных имён и управляющих символов.")
    try:
        if len(name.encode("utf-8")) > 255:
            raise ValueError
    except (UnicodeError, ValueError):
        raise invalid("FILE_NAME_INVALID", "Имя файла должно быть корректным UTF-8 длиной до 255 байт.") from None
    if Path(name).suffix.lower() not in EXTENSIONS:
        raise invalid("MEDIA_FORMAT_UNSUPPORTED", "Разрешены только WAV, MP3, PNG и JPEG.")
    return name.casefold()


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def checked_path(path: Path, root: Path) -> Path:
    """Проверяет каждый существующий компонент, не следуя ссылкам."""
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    if not path.is_relative_to(root):
        raise invalid("FILE_PATH_INVALID", "Файл должен находиться внутри разрешённого каталога.")
    current = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            current /= component
            if current.exists() or current.is_symlink():
                if is_link(current.lstat()):
                    raise invalid("FILE_LINK_UNSUPPORTED", "Ссылки файловой системы не поддерживаются. Передайте обычный файл.")
    except OSError:
        raise invalid("FILE_UNAVAILABLE", "Не удалось проверить путь файла. Проверьте доступ и повторите загрузку.", 503) from None
    return path


@contextmanager
def checked_open(path: Path, root: Path):
    path = checked_path(path, root)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or is_link(before) or before.st_nlink != 1:
            raise invalid("FILE_LINK_UNSUPPORTED", "Передайте обычный файл без символических и жёстких ссылок.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
            if identity(before) != identity(opened):
                raise invalid("FILE_CHANGED", "Файл изменился во время чтения. Повторите загрузку.")
            yield stream
            if identity(opened) != identity(os.fstat(stream.fileno())) or identity(opened) != identity(path.lstat()):
                raise invalid("FILE_CHANGED", "Файл изменился во время чтения. Повторите загрузку.")
    except RepositoryError:
        raise
    except OSError:
        raise invalid("FILE_UNAVAILABLE", "Файл недоступен. Проверьте набор и повторите загрузку.", 503) from None


def file_digest(path: Path, root: Path) -> tuple[str, int]:
    digest, size = sha256(), 0
    with checked_open(path, root) as stream:
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def validate_upload(upload, material: MaterialInput, workspace) -> RegisteredMaterial:
    validate_filename(material.path)
    if upload.original_filename != material.path:
        raise invalid("MEDIA_FILES_MISMATCH", "Имена переданных файлов должны точно совпадать с manifest.")
    workspace.check_upload(upload, complete=True)
    expected = EXTENSIONS[Path(material.path).suffix.lower()]
    if expected != material.mime_type:
        raise invalid("MEDIA_TYPE_MISMATCH", "Расширение, вид материала и MIME в manifest должны совпадать.")
    limit = MAX_AUDIO_BYTES if material.kind == "audio" else MAX_IMAGE_BYTES
    with checked_open(upload.temporary_path, workspace.folder) as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > limit:
            raise invalid("MEDIA_FILE_TOO_LARGE", "Аудио должно быть до 10 МиБ, изображение — до 5 МиБ.", 413)
        if size == 0:
            raise invalid("MEDIA_FILE_INVALID", "Пустой файл не является допустимым медиа.")
        signature = stream.read(12)
        stream.seek(0)
        try:
            if expected == "audio/wav":
                if signature[:4] != b"RIFF" or signature[8:12] != b"WAVE" or int.from_bytes(signature[4:8], "little") + 8 != size:
                    raise ValueError
                with wave.open(stream, "rb") as audio:
                    frames, rate = audio.getnframes(), audio.getframerate()
                    if frames <= 0 or rate <= 0 or frames / rate > workspace.max_audio_seconds:
                        raise ValueError
                    remaining = frames * audio.getnchannels() * audio.getsampwidth()
                    while chunk := audio.readframes(4096):
                        remaining -= len(chunk)
                    if remaining != 0:
                        raise ValueError
            elif expected == "audio/mpeg":
                from mutagen.mp3 import MP3
                if not (signature.startswith(b"ID3") or signature[:1] == b"\xff" and signature[1] & 0xE0 == 0xE0):
                    raise ValueError
                audio = MP3(stream)
                if not (math.isfinite(audio.info.length) and 0 < audio.info.length <= workspace.max_audio_seconds
                        and audio.info.sample_rate > 0 and audio.info.bitrate > 0):
                    raise ValueError
            else:
                from PIL import Image
                if expected == "image/png" and signature[:8] != b"\x89PNG\r\n\x1a\n" or expected == "image/jpeg" and signature[:3] != b"\xff\xd8\xff":
                    raise ValueError
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    def check(image):
                        if (image.format != ("PNG" if expected == "image/png" else "JPEG")
                                or min(image.size) <= 0 or image.width * image.height > workspace.max_image_pixels
                                or getattr(image, "n_frames", 1) != 1):
                            raise ValueError
                    with Image.open(stream) as image:
                        check(image)
                        image.verify()
                    stream.seek(0)
                    with Image.open(stream) as image:
                        check(image)
                        image.load()
        except ImportError:
            raise invalid("MEDIA_PARSER_NOT_CONFIGURED", "Парсер медиа не установлен. Подготовьте закреплённые зависимости сервера.", 503) from None
        except Exception:
            raise invalid("MEDIA_FILE_INVALID", "Формат или метаданные файла некорректны. Проверьте файл, длительность до 60 секунд и размер изображения до 25 Мп.") from None
    digest, actual_size = file_digest(upload.temporary_path, workspace.folder)
    workspace.check_upload(upload, complete=True)
    asset = ApprovedAsset(material.asset_id, upload.temporary_path, digest, expected)
    return RegisteredMaterial(asset_id=asset.asset_id, kind=material.kind, content_hash=digest,
        storage_path=workspace.storage_path(upload, expected), original_filename=material.path,
        mime_type=expected, byte_size=actual_size)
