from io import BytesIO
import os
import unicodedata

import pytest

from app.config import Settings
from app.db import initialize
from app.ingestion.importer import import_tickets
from app.ingestion.media_files import open_import_workspace
from app.repositories import Repository, RepositoryError
from app.security import checked_open, validate_filename
from scripts.media_import_fixture import manifest_for, synthetic_files, wav_bytes


@pytest.fixture
def media_context(tmp_path):
    settings = Settings(_env_file=None, app_db_path=tmp_path / "media.db",
                        app_tmp_root=tmp_path / "temporary", app_upload_root=tmp_path / "uploads")
    repo = Repository(settings.app_db_path)
    initialize(repo.path)
    return settings, repo


def import_files(context, files, *, manifest=None, preview=False, order=None):
    settings, repo = context
    with open_import_workspace(settings, repo, dry_run=preview) as workspace:
        for name in order or files:
            workspace.stage_stream(name, BytesIO(files[name]))
        return import_tickets(manifest or manifest_for(files), workspace.uploads, dry_run=preview,
                              repository=repo, workspace=workspace)


@pytest.mark.parametrize("name", ["../a.wav", "C:a.wav", "a/b.wav", "a\\b.wav", ".hidden.wav", "a.wav.",
    "a.wav ", "CON.wav", "nul.mp3", "NUL .wav", "COM1 .png", "AUX.jpg", "COM1.png", "LPT9.wav", "COM¹.wav", "a\x00.wav",
    "a\n.wav", "a?.wav", "a*.wav", "a\u202e.wav", "a.zip", unicodedata.normalize("NFD", "é.wav"), "я" * 130 + ".wav"])
def test_dangerous_names_are_rejected(name):
    with pytest.raises(RepositoryError):
        validate_filename(name)


def test_all_formats_preview_without_persistent_effect(media_context, monkeypatch):
    from app.gateways import UnavailableGateways
    for name in ["generate", "transcribe", "describe"]:
        monkeypatch.setattr(UnavailableGateways, name, lambda *a: pytest.fail("Model called during import"))
    settings, repo = media_context
    before = repo.path.read_bytes()
    response = import_files(media_context, synthetic_files(), preview=True)
    assert response.can_commit and response.counts.inserted == 4
    assert repo.path.read_bytes() == before and repo.list_imports() == []
    assert not settings.app_upload_root.exists() and not settings.app_tmp_root.exists()


@pytest.mark.parametrize("name", ["call.wav", "call.mp3", "screen.png", "photo.jpg"])
def test_signatures_and_parseable_metadata_required(media_context, name):
    files = {name: b"not media SENSITIVE_SENTINEL"}
    response = import_files(media_context, files, preview=True)
    assert not response.can_commit and response.errors[0].code == "MEDIA_FILE_INVALID"
    assert "SENSITIVE_SENTINEL" not in response.model_dump_json()
    assert media_context[1].list_imports() == []


def test_audio_duration_boundary_and_truncated_payload(media_context):
    assert import_files(media_context, {"call.wav": wav_bytes(60)}, preview=True).can_commit
    assert not import_files(media_context, {"call.wav": wav_bytes(60.001)}, preview=True).can_commit
    assert not import_files(media_context, {"call.wav": wav_bytes()[:-2]}, preview=True).can_commit


def test_image_pixels_boundary_and_stricter_file_limit(media_context, monkeypatch):
    from PIL import Image
    from app import security
    context = (media_context[0].model_copy(update={"app_max_image_pixels": 256}), media_context[1])
    files = {"screen.png": synthetic_files()["screen.png"]}
    assert import_files(context, files, preview=True).can_commit
    stream = BytesIO()
    Image.new("RGB", (257, 1)).save(stream, format="PNG")
    assert not import_files(context, {"screen.png": stream.getvalue()}, preview=True).can_commit
    monkeypatch.setattr(security, "MAX_IMAGE_BYTES", len(files["screen.png"]) - 1)
    with pytest.raises(RepositoryError) as exc:
        import_files(media_context, files, preview=True)
    assert exc.value.status_code == 413


def test_external_links_and_hardlinks_cannot_be_opened(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "private.wav"
    outside.write_bytes(wav_bytes())
    (root / "call.wav").symlink_to(outside)
    with pytest.raises(RepositoryError), checked_open(root / "call.wav", root):
        pytest.fail("Followed symlink")
    (root / "call.wav").unlink()
    os.link(outside, root / "call.wav")
    with pytest.raises(RepositoryError), checked_open(root / "call.wav", root):
        pytest.fail("Read hardlink")
    with pytest.raises(RepositoryError), checked_open(outside, root):
        pytest.fail("Read outside dataset")


def test_casefold_collision_and_staged_replacement(media_context):
    settings, repo = media_context
    with open_import_workspace(settings, repo, dry_run=True) as workspace:
        upload = workspace.stage_stream("Call.wav", BytesIO(wav_bytes()))
        with pytest.raises(RepositoryError) as duplicate:
            workspace.stage_stream("call.WAV", BytesIO(wav_bytes()))
        assert duplicate.value.code == "FILE_NAME_DUPLICATE"
        upload.temporary_path.write_bytes(wav_bytes(sample=1))
        result = import_tickets(manifest_for({"Call.wav": b""}), [upload], dry_run=True, repository=repo, workspace=workspace)
        assert not result.can_commit and result.errors[0].code == "FILE_CHANGED"


def test_intake_byte_limits_enforced_while_reading(media_context):
    from app.security import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_MEDIA_BYTES
    settings, repo = media_context
    with open_import_workspace(settings, repo, dry_run=True) as workspace:
        block = bytes(MAX_AUDIO_BYTES)
        for index in range(MAX_MEDIA_BYTES // MAX_AUDIO_BYTES):
            workspace.stage_stream(f"{index}.wav", BytesIO(block))
        with pytest.raises(RepositoryError) as error:
            workspace.stage_stream("extra.wav", BytesIO(b"x"))
        assert error.value.status_code == 413
    with open_import_workspace(settings, repo, dry_run=True) as workspace:
        upload, stream = workspace.begin_upload("screen.png")
        workspace.write_upload(upload, stream, bytes(MAX_IMAGE_BYTES))
        with pytest.raises(RepositoryError) as error:
            workspace.write_upload(upload, stream, b"x")
        assert error.value.status_code == 413
    assert not settings.app_tmp_root.exists()


def test_thirty_files_allowed_but_not_thirty_one(media_context):
    files = {f"call-{index}.wav": wav_bytes() for index in range(30)}
    assert import_files(media_context, files, preview=True).counts.inserted == 30
    files["extra.wav"] = wav_bytes()
    with pytest.raises(RepositoryError) as error:
        import_files(media_context, files, preview=True)
    assert error.value.status_code == 413
