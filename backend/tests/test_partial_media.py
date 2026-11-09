from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.gateways import GatewayUnavailable, MediaText
from app.ingestion.media_normalizer import MediaNormalizer
from app.media_replay import (
    MediaRecording, MediaRecordingSet, MediaReplayGateway, asset_details, make_media_record,
    media_request, publish_media_recordings,
)
from app.media_runtime import resolve_registered_asset
from app.processing_schemas import AnalysisInput, MaterialCoverage, NormalizationResult, SourceCoverage, analysis_input
from app.replay import semantic_hash
from scripts.partial_media_fixture import prepare_media_demo


class CountingReplay:
    def __init__(self, gateway):
        self.gateway, self.calls = gateway, []

    def transcribe(self, asset):
        self.calls.append(asset.asset_id)
        return self.gateway.transcribe(asset)

    def describe(self, asset):
        self.calls.append(asset.asset_id)
        return self.gateway.describe(asset)


def normalizer(demo, *, gateway=None, cache=True):
    gateway = gateway or MediaReplayGateway(demo.recordings, demo.profile, allowed_root=demo.settings.app_upload_root)
    return MediaNormalizer(demo.repo, demo.settings, demo.profile, asr_gateway=gateway, vision_gateway=gateway,
                           cache_root=demo.folder / "cache" if cache else None)


def normalize(demo, service=None):
    source = demo.repo.get_source(demo.ticket.source_id)
    return (service or normalizer(demo)).normalize_ticket(demo.ticket, content_hash=source.content_hash, source_version=1)


def test_partial_retry_reopen_reuses_success_and_preserves_source_and_revision(tmp_path):
    demo = prepare_media_demo(tmp_path)
    damaged = demo.repo.source_materials(demo.ticket.source_id)[-1]
    path = demo.settings.app_upload_root / damaged.storage_path
    raw = path.read_bytes()
    path.write_bytes(b"SYNTHETIC-BROKEN")
    gateway = CountingReplay(MediaReplayGateway(demo.recordings, demo.profile, allowed_root=demo.settings.app_upload_root))
    before = demo.repo.path.read_bytes()
    first = normalize(demo, normalizer(demo, gateway=gateway))
    assert first.context.data_origin == "imported" and all(m.metadata.origin == "authored" for m in first.materials if m.metadata)
    assert first.state == "partial" and first.coverage.model_dump() == dict(unit="materials", total=5, successful=4, failed=1, empty=0, usable=4, ratio=.8)
    assert len(gateway.calls) == 3 and first.document.errors[0].asset_id == damaged.asset_id
    projected = analysis_input(first)
    assert projected and projected.coverage == first.coverage and projected.source_errors == first.document.errors
    assert not projected.document.errors and all(f.asset_id != damaged.asset_id for f in projected.document.fragments)
    saved = first.model_dump_json()
    path.write_bytes(raw)
    second = normalize(demo, normalizer(demo, gateway=gateway))
    assert len(gateway.calls) == 4  # Только восстановленный материал, прежние success взяты из cache.
    assert second.state == "complete" and second.coverage.successful == 5
    assert second.revision_id != first.revision_id and second.analysis_digest != first.analysis_digest
    assert set(f.fragment_id for f in first.document.fragments) <= set(f.fragment_id for f in second.document.fragments)
    assert NormalizationResult.model_validate_json(saved) == first
    third = normalize(demo, normalizer(demo, gateway=gateway))
    assert third == second and len(gateway.calls) == 4
    assert demo.repo.path.read_bytes() == before and demo.repo.get_source(demo.ticket.source_id).document is None
    assert demo.ticket.text == demo.repo.get_source(demo.ticket.source_id).ticket.text


@pytest.mark.parametrize("field", ["ratio", "successful", "empty", "unit", "boolean-count"])
def test_coverage_counts_have_explicit_units_and_invariants(field):
    value = dict(total=2, successful=1, failed=1, empty=0, usable=1, ratio=.5)
    if field == "ratio": value[field] = 1
    elif field == "unit": value[field] = "sources"
    elif field == "boolean-count": value["total"] = True
    else: value[field] += 1
    with pytest.raises(ValidationError): MaterialCoverage(**value)
    with pytest.raises(ValidationError): SourceCoverage(total=2, complete=1, partial=0, failed=0, empty=0)


@pytest.mark.parametrize("kind", ["revision", "digest", "context", "drop-error", "forge-fragment", "coverage"])
def test_revision_envelope_cannot_claim_another_result(tmp_path, kind):
    demo = prepare_media_demo(tmp_path, omit={"fixture-screen-png"})
    value = normalize(demo).model_dump(mode="json")
    if kind == "revision": value["revision_id"] = "0" * 64
    elif kind == "digest": value["analysis_digest"] = "0" * 64
    elif kind == "context": value["context"]["source_version"] = 2
    elif kind == "drop-error": value["document"]["errors"] = []
    elif kind == "forge-fragment": value["document"]["fragments"][0]["asset_id"] = "foreign"
    else: value["coverage"]["failed"] = 0
    with pytest.raises(ValidationError): NormalizationResult.model_validate(value)


def test_analysis_projection_cannot_hide_errors_or_forge_digest(tmp_path):
    demo = prepare_media_demo(tmp_path, omit={"fixture-screen-png"})
    result = normalize(demo)
    value = analysis_input(result).model_dump()
    value["source_errors"] = []
    with pytest.raises(ValidationError): AnalysisInput(**value)
    value = analysis_input(result).model_dump()
    value["document"]["fragments"][0]["text"] = "Другой контекст"
    with pytest.raises(ValidationError): AnalysisInput(**value)


def test_all_failed_and_successful_empty_are_different(tmp_path):
    demo = prepare_media_demo(tmp_path, text="")
    class Failing:
        def transcribe(self, asset): raise GatewayUnavailable("MODEL_TIMEOUT", "Do not expose SENSITIVE_SENTINEL")
        describe = transcribe
    failed = normalize(demo, normalizer(demo, gateway=Failing()))
    assert failed.state == "failed" and failed.coverage.failed == 4 and analysis_input(failed) is None
    assert "SENSITIVE_SENTINEL" not in failed.model_dump_json()
    assert not (demo.folder / "cache").exists()
    records = [make_media_record(r.request, {"fragments": []}, demo.profile, origin="authored") for r in demo.records]
    empty_path = demo.folder / "empty.json"
    publish_media_recordings(empty_path, records, demo.profile)
    empty = normalize(demo, normalizer(demo, gateway=MediaReplayGateway(empty_path, demo.profile, allowed_root=demo.settings.app_upload_root), cache=False))
    assert empty.state == "empty" and empty.coverage.empty == 4 and empty.coverage.failed == 0
    assert not empty.document.errors and analysis_input(empty) is None


@pytest.mark.parametrize("kind", ["unknown-bytes", "foreign-id", "wrong-mime", "profile", "missing-set", "live"])
def test_replay_has_no_approximate_or_live_fallback(tmp_path, kind):
    demo = prepare_media_demo(tmp_path)
    asset = resolve_registered_asset(demo.repo, demo.settings, source_id=demo.ticket.source_id, source_version=1, asset_id=demo.ticket.assets[0].asset_id)
    profile, path = demo.profile, demo.recordings
    if kind in {"unknown-bytes", "foreign-id", "wrong-mime"}:
        from dataclasses import replace
        if kind == "unknown-bytes":
            from scripts.media_import_fixture import wav_bytes
            asset.absolute_path.write_bytes(wav_bytes(sample=3))
            asset = replace(asset, content_hash=sha256(asset.absolute_path.read_bytes()).hexdigest())
        elif kind == "foreign-id": asset = replace(asset, asset_id="foreign")
        else: asset = replace(asset, mime_type="image/png")
    elif kind == "profile":
        profile = profile.model_copy(deep=True)
        profile.asr.parameters["temperature"] = 0
    elif kind == "missing-set": path = demo.folder / "missing.json"
    else: profile = profile.model_copy(update={"mode": "live"})
    with pytest.raises(GatewayUnavailable) as exc:
        MediaReplayGateway(path, profile, allowed_root=demo.settings.app_upload_root).transcribe(asset)
    assert exc.value.code == {
        "unknown-bytes": "REPLAY_RECORD_MISSING", "foreign-id": "REPLAY_RECORD_MISSING",
        "wrong-mime": "MEDIA_REQUEST_INVALID", "profile": "MEDIA_REPLAY_PROFILE_CONFLICT",
        "missing-set": "MEDIA_REPLAY_SET_INVALID", "live": "MEDIA_LIVE_NOT_CONNECTED",
    }[kind]
    assert len(list(demo.folder.glob("missing*"))) == 0


@pytest.mark.parametrize("field", ["schema_hash", "request-hash", "origin", "model", "params", "unmasked", "foreign-asset", "boolean-time"])
def test_record_validation_rejects_forged_inputs(tmp_path, field):
    demo = prepare_media_demo(tmp_path)
    value = demo.records[0].model_dump(mode="json")
    if field == "schema_hash": value[field] = "0" * 64
    elif field == "request-hash": value["metadata"]["request_hash"] = "0" * 64
    elif field == "origin": value["metadata"]["origin"] = "imported"
    elif field == "model": value["metadata"]["model_id"] = "forged"
    elif field == "params": value["metadata"]["parameters"]["tamper"] = True
    elif field == "unmasked": value["response"]["fragments"][0]["text"] += " synthetic@example.invalid"
    elif field == "foreign-asset": value["response"]["fragments"][0]["asset_id"] = "foreign"
    else: value["response"]["fragments"][0].update(start_ms=True, end_ms=50)
    from app.media_replay import validate_media_record
    with pytest.raises((GatewayUnavailable, ValidationError)):
        validate_media_record(MediaRecording.model_validate(value), demo.profile)


def test_timestamp_bounds_use_actual_audio_duration(tmp_path):
    demo = prepare_media_demo(tmp_path)
    record = next(r for r in demo.records if r.request.mime_type == "audio/wav")
    raw = record.response.model_dump()
    raw["fragments"][0].update(start_ms=0, end_ms=500)
    bad = make_media_record(record.request, raw, demo.profile, origin="authored")
    path = demo.folder / "bad-time.json"
    publish_media_recordings(path, [bad], demo.profile)
    asset = resolve_registered_asset(demo.repo, demo.settings, source_id=demo.ticket.source_id, source_version=1, asset_id=record.request.asset_id)
    with pytest.raises(GatewayUnavailable) as exc:
        MediaReplayGateway(path, demo.profile, allowed_root=demo.settings.app_upload_root).transcribe(asset)
    assert exc.value.code == "MEDIA_TIMESTAMPS_INVALID"


def test_immutable_recordings_and_cache_corruption_are_visible(tmp_path):
    demo = prepare_media_demo(tmp_path)
    before = demo.recordings.read_bytes()
    assert publish_media_recordings(demo.recordings, demo.records, demo.profile)["record_count"] == 4
    changed = [make_media_record(r.request, {"fragments": []}, demo.profile, origin="authored") for r in demo.records]
    with pytest.raises(GatewayUnavailable): publish_media_recordings(demo.recordings, changed, demo.profile)
    assert demo.recordings.read_bytes() == before
    complete = normalize(demo)
    assert complete.state == "complete"
    cache = next((demo.folder / "cache").glob("*.json"))
    cache.write_text("{SENSITIVE_SENTINEL")
    damaged = normalize(demo)
    assert damaged.state == "partial" and damaged.document.errors[0].code == "MEDIA_CHECKPOINT_INVALID"
    assert "SENSITIVE_SENTINEL" not in damaged.model_dump_json() and cache.read_text() == "{SENSITIVE_SENTINEL"


def test_duplicate_json_keys_and_checksum_changes_are_rejected(tmp_path):
    demo = prepare_media_demo(tmp_path)
    gateway = MediaReplayGateway(demo.recordings, demo.profile, allowed_root=demo.settings.app_upload_root)
    saved = demo.recordings.read_text()
    demo.recordings.write_text('{"format_version":"media-recordings-v1",' + saved[1:])
    with pytest.raises(GatewayUnavailable): gateway.records()
    value = json.loads(saved)
    key = next(iter(value["checksums"]))
    value["checksums"][key] = "0" * 64
    demo.recordings.write_text(json.dumps(value))
    with pytest.raises(GatewayUnavailable): gateway.records()


def test_normalizer_and_replay_use_no_network(tmp_path, monkeypatch):
    import socket
    import httpx
    demo = prepare_media_demo(tmp_path)
    def forbidden(*args, **kwargs): pytest.fail("Network call in media replay")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    assert normalize(demo).state == "complete"


def test_masking_before_normalized_output_recordings_and_cache(tmp_path):
    fixture = json.loads((Path(__file__).parents[2] / "data/security/synthetic-safety.json").read_text(encoding="utf-8"))
    raw = " ".join(item["raw"] for item in fixture["PII_SAMPLE"])
    demo = prepare_media_demo(tmp_path, text=raw)
    result = normalize(demo)
    serialized = result.model_dump_json() + demo.recordings.read_text() + "".join(p.read_text() for p in (demo.folder / "cache").glob("*.json"))
    for case in fixture["PII_SAMPLE"]:
        assert case["raw"] not in serialized and case["masked"] in result.document.fragments[0].text
    assert "synthetic@example.invalid" not in serialized
    assert demo.repo.get_source(demo.ticket.source_id).ticket.text == raw


def test_wrong_context_and_live_normalizer_are_rejected(tmp_path):
    demo = prepare_media_demo(tmp_path)
    source = demo.repo.get_source(demo.ticket.source_id)
    from app.repositories import RepositoryError
    with pytest.raises(RepositoryError) as invalid:
        normalizer(demo).normalize_ticket(demo.ticket, source_version=True, content_hash=source.content_hash)
    assert invalid.value.code == "NORMALIZATION_CONTEXT_INVALID"
    changed = demo.ticket.model_copy(update={"text": "Changed source"})
    with pytest.raises(RepositoryError) as exc:
        normalizer(demo).normalize_ticket(changed, source_version=1, content_hash=source.content_hash)
    assert exc.value.code == "NORMALIZATION_IDENTITY_CONFLICT"
    live = demo.profile.model_copy(update={"mode": "live"})
    with pytest.raises(GatewayUnavailable) as exc:
        MediaNormalizer(demo.repo, demo.settings, live, asr_gateway=None, vision_gateway=None)
    assert exc.value.code == "MEDIA_LIVE_NOT_CONNECTED"


def test_published_partial_examples_validate_and_exported_legacy_models_stay_separate():
    from app.cli import PROCESSING_SCHEMAS
    from app.config import REPO_ROOT
    examples = json.loads((REPO_ROOT / "data/contracts/partial-processing.json").read_text(encoding="utf-8"))
    assert examples["synthetic"] and examples["origin"] == "authored" and not examples["production_pipeline_connected"]
    for example in examples["examples"]:
        PROCESSING_SCHEMAS[example["schema"]].model_validate(example["value"])
    assert len(examples["examples"]) == 8
