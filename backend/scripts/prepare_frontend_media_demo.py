"""Явно создаёт отдельный synthetic/authored demo; не меняет рабочий .env/SQLite."""

import json
from uuid import uuid4

from app.config import REPO_ROOT
from scripts.media_import_fixture import synthetic_files
from scripts.partial_pipeline_fixture import prepare_partial_ui_demo


def main():
    folder = REPO_ROOT / "var" / ("frontend-media-demo-" + uuid4().hex)
    demo, profile, card = prepare_partial_ui_demo(folder)
    selected = folder / "selected-files"
    selected.mkdir()
    for name, raw in synthetic_files().items():
        (selected / name).write_bytes(raw)
    manifest = selected / "manifest.json"
    manifest.write_text(json.dumps(dict(dataset_id="synthetic-partial-media-v1", synthetic=True,
        tickets=[demo.ticket.model_dump(mode="json")]), ensure_ascii=False, indent=2), encoding="utf-8")
    env = dict(APP_DB_PATH=str(demo.repo.path), APP_UPLOAD_ROOT=str(demo.settings.app_upload_root), APP_TMP_ROOT=str(demo.settings.app_tmp_root),
        APP_MODEL_MODE="replay", APP_PROCESSING_PROFILE_PATH=str(demo.settings.app_processing_profile_path),
        APP_RECORDINGS_ROOT=str(demo.settings.app_recordings_root), APP_MEDIA_RECORDINGS_PATH=str(demo.recordings),
        APP_MEDIA_CACHE_ROOT=str(demo.settings.app_media_cache_root), APP_REPLAY_MODEL_ID=profile.model_id,
        APP_REPLAY_NORMALIZATION_HASH=profile.parameters["normalization_hash"], TEXT_MODEL_BASE_URL="", ASR_BASE_URL="", VL_BASE_URL="")
    result = dict(synthetic=True, origin="authored", live_model_calls=False, folder=str(folder), manifest=str(manifest),
        selected_files=str(selected), topic_id=card.topic.topic_id, source_id=demo.ticket.source_id, environment=env)
    (folder / "demo-settings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
