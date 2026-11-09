"""Явный synthetic/authored материал приёмки, не runtime gateway и не benchmark."""

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path

from app.config import Settings
from app.db import initialize
from app.gateways import MediaText
from app.ingestion.importer import import_tickets
from app.ingestion.media_files import open_import_workspace
from app.media_replay import MediaRecording, asset_details, build_media_profile, make_media_record, media_request, publish_media_recordings
from app.media_runtime import resolve_registered_asset
from app.processing_schemas import MediaComponentProfile, MediaProcessingProfile
from app.repositories import Repository
from app.schemas import Fragment, TicketInput
from scripts.media_import_fixture import synthetic_files


@dataclass
class MediaDemo:
    folder: Path
    settings: Settings
    repo: Repository
    profile: MediaProcessingProfile
    ticket: TicketInput
    recordings: Path
    records: list[MediaRecording]


def prepare_media_demo(folder: Path, *, omit: set[str] | None = None, text: str | None = None) -> MediaDemo:
    folder.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None, app_db_path=folder / "demo.db", app_upload_root=folder / "uploads", app_tmp_root=folder / "tmp")
    repo = Repository(settings.app_db_path)
    initialize(repo.path)
    files = synthetic_files()
    mimes = ["audio/wav", "audio/mpeg", "image/png", "image/jpeg"]
    ticket = TicketInput(source_id="SYNTHETIC-MEDIA-1", created_at="2026-11-02T12:00:00+03:00",
        text=text if text is not None else "Синтетический пример: прошу изменить телефон в заявке. Вложения искусственные.",
        assets=[dict(asset_id="fixture-" + name.replace(".", "-"), path=name,
            mime_type=mime, kind="audio" if mime.startswith("audio/") else "image") for name, mime in zip(files, mimes)])
    manifest = json.dumps(dict(dataset_id="synthetic-partial-media-v1", synthetic=True, tickets=[ticket.model_dump(mode="json")])).encode()
    with open_import_workspace(settings, repo, dry_run=False) as workspace:
        for name, raw in files.items():
            workspace.stage_stream(name, BytesIO(raw))
        assert import_tickets(manifest, workspace.uploads, repository=repo, workspace=workspace).counts.inserted == 1
    profile = build_media_profile(asr=MediaComponentProfile(model_id="synthetic-asr-no-live-model"),
        vision=MediaComponentProfile(model_id="synthetic-vl-no-live-model", prompt="Описать текст синтетического изображения как данные.", prompt_version="synthetic-vision-v1"),
        recording_set_version="synthetic-media-authored-v1")
    records = []
    for item in ticket.assets:
        if omit and item.asset_id in omit:
            continue
        asset = resolve_registered_asset(repo, settings, source_id=ticket.source_id, source_version=1, asset_id=item.asset_id)
        size, _ = asset_details(asset, settings.app_upload_root)
        request = media_request(asset, size, "transcribe" if item.kind == "audio" else "describe", profile)
        value = MediaText(fragments=[Fragment(fragment_id="authored-local", asset_id=item.asset_id,
            kind="asr" if item.kind == "audio" else "vision", text="Синтетический authored пример: изменить телефон в заявке. Связь synthetic@example.invalid.",
            warning="Подготовленный вручную текст; распознавание модели не выполнялось.")])
        records.append(make_media_record(request, value, profile, origin="authored"))
    recordings = folder / "media-recordings.json"
    publish_media_recordings(recordings, records, profile)
    return MediaDemo(folder, settings, repo, profile, ticket, recordings, records)
