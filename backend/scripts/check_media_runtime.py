"""Только synthetic: постоянные assets/версии/решения без gateway и рабочей БД."""

from hashlib import sha256
from io import BytesIO
import json
from uuid import uuid4

from app.config import REPO_ROOT, Settings
from app.db import initialize
from app.ingestion.importer import import_tickets
from app.ingestion.media_files import open_import_workspace
from app.media_runtime import resolve_registered_asset
from app.repositories import Repository, RepositoryError
from app.schemas import DecisionUpdate
from scripts.media_import_fixture import manifest_for, seed_authored_topic, synthetic_files, wav_bytes


def main() -> None:
    private = [REPO_ROOT / ".env", *sorted((REPO_ROOT / "var").glob("*.db"))]
    protected = {path: sha256(path.read_bytes()).hexdigest() for path in private if path.is_file()}
    folder = REPO_ROOT / "var" / ("media-runtime-check-" + uuid4().hex)
    folder.mkdir(parents=True)
    settings = Settings(_env_file=None, app_db_path=folder / "check.db", app_upload_root=folder / "uploads",
                        app_tmp_root=folder / "temporary")
    repo = Repository(settings.app_db_path)
    initialize(repo.path)
    files = synthetic_files()

    def register(values):
        with open_import_workspace(settings, repo, dry_run=False) as workspace:
            for name, raw in values.items():
                workspace.stage_stream(name, BytesIO(raw))
            return import_tickets(manifest_for(values), workspace.uploads, repository=repo, workspace=workspace)

    def resolve(index=1, version=1):
        return resolve_registered_asset(Repository(repo.path), settings, source_id=f"SYNTHETIC-MEDIA-{index}",
                                        source_version=version, asset_id=f"synthetic-asset-{index}")

    assert register(files).counts.inserted == 4
    before = repo.path.read_bytes()
    assets = [resolve(index) for index in range(1, 5)]
    assert [asset.absolute_path.read_bytes() for asset in assets] == list(files.values())
    assert all(asset.content_hash == sha256(raw).hexdigest() for asset, raw in zip(assets, files.values()))
    assert repo.path.read_bytes() == before
    assert register(files).counts.unchanged == 4
    print("synthetic=true; formats=WAV/MP3/PNG/JPEG; resolver=read-only; models_called=false")
    print("resolve: 4 registered files; exact source/version/asset/hash; unchanged repeat")
    topic_id = seed_authored_topic(repo)
    repo.record_decision(topic_id, DecisionUpdate(version=1, status="needs_research", owner="Синтетический владелец",
                                                reason="Проверить исторический материал"))
    saved = repo.get_topic(topic_id, "replay")
    files["call.wav"] = wav_bytes(sample=3)
    delta = register(files)
    assert delta.counts.updated == 1 and delta.counts.unchanged == 3
    assert resolve() == assets[0]
    current = resolve(version=2)
    assert current.absolute_path != assets[0].absolute_path and current.content_hash != assets[0].content_hash
    assert repo.get_topic(topic_id, "replay").history == saved.history
    assert repo.get_topic(topic_id, "replay").topic.owner == saved.topic.owner
    print("delta/reopen: v1 and v2 resolve distinct persistent files; decision/history retained")
    # Повреждаем только собственную synthetic копию и возвращаем её точные bytes.
    before = repo.path.read_bytes()
    raw = current.absolute_path.read_bytes()
    try:
        current.absolute_path.write_bytes(wav_bytes(sample=4))
        try:
            resolve(version=2)
        except RepositoryError as exc:
            assert exc.code == "REGISTERED_MATERIAL_INTEGRITY_FAILED"
        else:
            raise AssertionError("Changed file was approved")
    finally:
        current.absolute_path.write_bytes(raw)
    try:
        resolve_registered_asset(repo, settings, source_id="SYNTHETIC-MEDIA-1", source_version=2, asset_id="synthetic-asset-2")
    except RepositoryError as exc:
        assert exc.code == "SOURCE_ASSET_NOT_FOUND"
    else:
        raise AssertionError("Foreign material was approved")
    assert resolve(version=2) == current and repo.path.read_bytes() == before
    assert all(path.is_file() and sha256(path.read_bytes()).hexdigest() == digest for path, digest in protected.items())
    result = {"synthetic": True, "models_called": False, "recognition_integrated": False, "formats": 4,
              "readonly_resolution": True, "historical_version_preserved": True, "decision_history_preserved": True,
              "changed_file_rejected": True, "foreign_asset_rejected": True, "working_private_files_preserved": True}
    report = folder / "result.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("PASS: exact versions, tamper/foreign rejection, reopen and private data preserved; media analysis unavailable")
    print("report:", report.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
