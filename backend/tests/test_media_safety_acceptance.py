"""Синтетическая приёмка У1: недоверенные media-ответы и плохие payload."""

import json
from pathlib import Path
import socket
import subprocess

import httpx
import pytest

from app.analysis.extractor import DEFAULT_PROMPT_PATH, NeedExtractor
from app.config import REPO_ROOT
from app.gateways import GatewayUnavailable, TextRequest
from app.ingestion.media_normalizer import MediaNormalizer
from app.media_replay import MediaReplayGateway, make_media_record, publish_media_recordings
from app.partial_integration import build_partial_profile
from app.processing_schemas import analysis_input
from app.replay import ReplayGateway, make_record, publish_records
from app.repositories import RepositoryError
from app.schemas import ExtractedNeed, ExtractedNeeds
from scripts.partial_media_fixture import prepare_media_demo
from test_upload_security import media_context, import_files


FIXTURE = json.loads((REPO_ROOT / "data/security/synthetic-media-safety.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["UPLOAD_BAD"], ids=lambda c: c["id"])
@pytest.mark.parametrize("preview", [True, False], ids=["preview", "commit"])
def test_hostile_media_payload_cannot_register_or_leak(media_context, case, preview):
    settings, repo = media_context
    before = repo.path.read_bytes()
    payload = bytes.fromhex(case["hex"])
    if preview:
        result = import_files(media_context, {case["name"]: payload}, preview=True)
        assert not result.can_commit and result.errors[0].code == "MEDIA_FILE_INVALID"
        assert "<script>" not in result.model_dump_json()
    else:
        with pytest.raises(RepositoryError) as error:
            import_files(media_context, {case["name"]: payload})
        assert error.value.code == "MEDIA_FILE_INVALID" and "<script>" not in str(error.value)
    assert repo.path.read_bytes() == before and repo.list_imports() == []
    assert repo.registered_materials() == []
    assert not settings.app_tmp_root.exists() and not settings.app_upload_root.exists()


@pytest.mark.parametrize("fixture_key", ["INJ_AUDIO", "INJ_IMAGE"])
def test_instructions_in_media_response_stay_data_through_mask_cache_and_extraction(tmp_path, monkeypatch, fixture_key):
    case = FIXTURE[fixture_key]
    demo = prepare_media_demo(tmp_path)
    records = []
    for old in demo.records:
        response = old.response.model_dump(mode="json")
        if response["fragments"][0]["kind"] == case["kind"]:
            response["fragments"][0]["text"] = case["text"]
            response["fragments"][0]["warning"] = "Синтетический тест synthetic@example.invalid"
        records.append(make_media_record(old.request, response, demo.profile, origin="authored"))
    path = tmp_path / "injection-media.json"
    publish_media_recordings(path, records, demo.profile)
    gateway = MediaReplayGateway(path, demo.profile, allowed_root=demo.settings.app_upload_root)
    normalizer = MediaNormalizer(demo.repo, demo.settings, demo.profile, asr_gateway=gateway,
        vision_gateway=gateway, cache_root=tmp_path / "checked-cache")
    before = demo.repo.path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Instruction attempted a network or command side effect")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    real_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if path.name == "private-synthetic.txt":
            pytest.fail("Instruction attempted to read a file")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    source = demo.repo.get_source(demo.ticket.source_id)
    result = normalizer.normalize_ticket(source.ticket, content_hash=source.content_hash, source_version=1)
    assert result.state == "complete" and result.coverage.successful == 5
    projected = analysis_input(result)
    fragment = next(f for f in projected.document.fragments if f.kind == case["kind"])
    assert "../private-synthetic.txt" in fragment.text and "[EMAIL]" in fragment.text
    serialized = result.model_dump_json() + path.read_text(encoding="utf-8")
    serialized += "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "checked-cache").glob("*.json"))
    assert "synthetic@example.invalid" not in serialized
    profile = build_partial_profile(demo.settings, media_profile=demo.profile,
        model_id="synthetic-safety-text-no-live", recording_set_version="synthetic-safety-v1")
    request = TextRequest(operation="extract_needs", prompt=DEFAULT_PROMPT_PATH.read_text(encoding="utf-8"),
        prompt_version="extract-needs-v1", data={"document": projected.document.model_dump(mode="json")}, parameters=profile.parameters)
    assert "../private-synthetic.txt" not in request.prompt
    need = ExtractedNeed(need_id="pending", source_id=demo.ticket.source_id, fragment_id=fragment.fragment_id,
        scenario="Контакт заявки", need_type="operational_request", statement="Изменить телефон в заявке",
        evidence_quote=case["need_quote"], explicit_request=True)
    def extracted(quote, folder):
        value = ExtractedNeeds(needs=[need.model_copy(update={"evidence_quote": quote})])
        record = make_record(request, ExtractedNeeds, value.model_dump(mode="json"), model_id=profile.model_id,
            normalization_hash=profile.parameters["normalization_hash"], set_version=profile.recording_set_version, origin="authored")
        publish_records(folder, [record])
        return NeedExtractor(ReplayGateway(folder, profile.model_id, profile.parameters["normalization_hash"]), profile).extract(projected.document)
    good = extracted(case["need_quote"], tmp_path / "known-text")
    assert good.metadata[0].origin == "authored" and good.needs[0].evidence_quote == case["need_quote"]
    with pytest.raises(GatewayUnavailable) as exc:
        extracted("Несуществующее доказательство выполнения команды", tmp_path / "forged-text")
    assert exc.value.code == "EXTRACTION_QUOTE_INVALID"
    assert demo.repo.path.read_bytes() == before and demo.repo.get_source(demo.ticket.source_id).ticket == demo.ticket
