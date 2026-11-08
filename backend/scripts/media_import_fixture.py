"""Только синтетические файлы для приёмки импорта, без записей людей и моделей."""

from io import BytesIO
import json
from pathlib import Path
import wave


def wav_bytes(seconds: float = .1, sample: int = 0) -> bytes:
    stream = BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * round(8000 * seconds))
    return stream.getvalue()


def synthetic_files() -> dict[str, bytes]:
    from PIL import Image
    files = {"call.wav": wav_bytes(),
             # MPEG-1 Layer III: 10 CBR кадров 128 kbps/44100 Hz с пустыми granules.
             # Это искусственная тишина, без речи; проверяет контейнер/метаданные.
             "call.mp3": (b"\xff\xfb\x90\x64" + bytes(413)) * 10}
    for name, format in [("screen.png", "PNG"), ("photo.jpg", "JPEG")]:
        output = BytesIO()
        Image.new("RGB", (16, 16), color=(10, 80, 140)).save(output, format=format)
        files[name] = output.getvalue()
    return files


def manifest_for(files: dict[str, bytes]) -> bytes:
    mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".png": "image/png", ".jpg": "image/jpeg"}
    tickets = []
    for index, name in enumerate(files, 1):
        kind = "audio" if name.endswith((".wav", ".mp3")) else "image"
        tickets.append({"source_id": f"SYNTHETIC-MEDIA-{index}", "product_id": "webneft",
            "created_at": "2026-10-15T12:00:00+03:00", "text": "Синтетический пример: прошу изменить телефон в заявке. Вложение искусственное.",
            "assets": [{"asset_id": f"synthetic-asset-{index}", "path": name, "kind": kind, "mime_type": mime[Path(name).suffix]}]})
    return json.dumps({"dataset_id": "synthetic-media-import-v1", "synthetic": True, "tickets": tickets}, ensure_ascii=False).encode()


def write_fixture(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    files = synthetic_files()
    for name, data in files.items():
        (folder / name).write_bytes(data)
    path = folder / "manifest.json"
    path.write_bytes(manifest_for(files))
    return path


def seed_authored_topic(repo) -> str:
    """Ручное grounding только по тексту; медиа не распознаётся и модель не вызывается."""
    from app.schemas import EvidenceRef, ExtractedNeed, Fragment, NormalizedDocument, PassportExample, Topic, TopicPassport
    source = repo.get_source("SYNTHETIC-MEDIA-1")
    text = source.ticket.text
    document = NormalizedDocument(source_id=source.ticket.source_id, version=source.source_version,
        content_hash=source.content_hash, normalization_version="synthetic-authored-import-check",
        fragments=[Fragment(fragment_id="synthetic-import-text-fragment", kind="text", text=text)])
    need = ExtractedNeed(need_id="synthetic-import-need", source_id=source.ticket.source_id,
        fragment_id=document.fragments[0].fragment_id, evidence_quote="прошу изменить телефон в заявке",
        scenario="Контакт в заявке", need_type="operational_request", statement="Изменить телефон в заявке", explicit_request=True)
    repo.save_source_analysis(document, [need])
    ref = EvidenceRef(source_id=need.source_id, fragment_id=need.fragment_id, quote=need.evidence_quote)
    topic_id = "synthetic-import-authored-topic"
    repo.create_topic(Topic(topic_id=topic_id, title="Синтетическая потребность изменения телефона",
        summary="Явно ручная тема для проверки сохранности решения при импорте.",
        passport=TopicPassport(scenario=need.scenario, need_statement=need.statement,
            scope="Только синтетический текст, без распознавания вложения", scope_confirmed=True, version=1,
            inclusion_examples=[PassportExample(text=ref.quote, evidence_refs=[ref])]),
        need_ids=[need.need_id], evidence=[ref]), origin="authored")
    return topic_id
