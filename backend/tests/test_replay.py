import copy
import json
import socket

import pytest
from pydantic import ValidationError

from app.gateways import GatewayResult, GatewayUnavailable, TextRequest, UnavailableGateways, model_health, text_gateway
from app.replay import (
    RecordingGateway, ReplayGateway, SCHEMAS, make_record, publish_records, request_key,
    semantic_hash, verify_recordings,
)
from app.schemas import BatchAssignment, ExtractedNeeds


def request():
    return TextRequest(operation="extract_needs", prompt="Синтетический пример контракта, не промпт анализа.",
        prompt_version="contract-only-1", data={"synthetic": True, "fragments": ["a", "b"]}, parameters={"temperature": 0})


def record(req=None, *, version="authored-1", response=None):
    return make_record(req or request(), ExtractedNeeds, response or {"needs": []}, model_id="synthetic-no-live-model",
        normalization_hash="a" * 64, set_version=version, origin="authored")


def test_replay_roundtrip_has_exact_provenance_and_no_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Replay called network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    publish_records(tmp_path, [record()])
    gateway = ReplayGateway(tmp_path, "synthetic-no-live-model", "a" * 64)
    result = gateway.generate(request(), ExtractedNeeds)
    assert result.value.needs == [] and result.metadata.origin == "authored"
    assert verify_recordings(tmp_path) == {"record_count": 1, "origins": ["authored"], "network_used": False}


@pytest.mark.parametrize("change", ["prompt", "version", "data", "order", "parameters", "model", "normalization", "schema"])
def test_replay_never_uses_similar_requests_or_fallback(tmp_path, change):
    publish_records(tmp_path, [record()])
    req = request()
    model, norm, schema = "synthetic-no-live-model", "a" * 64, ExtractedNeeds
    if change == "prompt": req = req.model_copy(update={"prompt": req.prompt + "!"})
    if change == "version": req = req.model_copy(update={"prompt_version": "contract-2"})
    if change == "data": req = req.model_copy(update={"data": {**req.data, "extra": True}})
    if change == "order": req = req.model_copy(update={"data": {**req.data, "fragments": ["b", "a"]}})
    if change == "parameters": req = req.model_copy(update={"parameters": {"temperature": 1}})
    if change == "model": model = "other"
    if change == "normalization": norm = "b" * 64
    if change == "schema": schema = BatchAssignment
    with pytest.raises(GatewayUnavailable) as exc:
        ReplayGateway(tmp_path, model, norm).generate(req, schema)
    assert exc.value.code == "REPLAY_RECORD_MISSING"


def test_object_key_order_is_canonical_but_arrays_are_ordered():
    req = request()
    other = req.model_copy(update={"data": dict(reversed(list(req.data.items())))})
    assert request_key(req, ExtractedNeeds, "m", "a" * 64) == request_key(other, ExtractedNeeds, "m", "a" * 64)


def test_tampered_response_and_provenance_are_rejected(tmp_path):
    publish_records(tmp_path, [record()])
    item = record()
    folder = tmp_path / json.loads((tmp_path / "active.json").read_text())["directory"]
    path = folder / (item.metadata.request_hash + ".json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["metadata"]["origin"] = "model"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(GatewayUnavailable) as exc:
        verify_recordings(tmp_path)
    assert exc.value.code == "REPLAY_SET_INVALID"


def test_bad_record_does_not_replace_active_set(tmp_path):
    publish_records(tmp_path, [record()])
    before = (tmp_path / "active.json").read_bytes()
    bad = record(version="new-version").model_copy(update={"response": {"needs": [], "unknown": True}})
    with pytest.raises(ValidationError):
        publish_records(tmp_path, [bad])
    assert (tmp_path / "active.json").read_bytes() == before
    with pytest.raises(GatewayUnavailable) as exc:
        publish_records(tmp_path, [record(request().model_copy(update={"prompt": "changed"}))])
    assert exc.value.code == "RECORDING_VERSION_CONFLICT"
    publish_records(tmp_path, [record(version="new-version")])
    assert list(tmp_path.glob("active.backup.*.json"))
    assert verify_recordings(tmp_path)["record_count"] == 1


def test_historical_recording_version_cannot_be_reused_with_other_content(tmp_path):
    publish_records(tmp_path, [record(version="v1")])
    publish_records(tmp_path, [record(version="v2")])
    before = (tmp_path / "active.json").read_bytes()
    changed = record(request().model_copy(update={"prompt": "changed semantic input"}), version="v1")
    with pytest.raises(GatewayUnavailable) as exc:
        publish_records(tmp_path, [changed])
    assert exc.value.code == "RECORDING_VERSION_CONFLICT"
    assert (tmp_path / "active.json").read_bytes() == before


def test_concurrent_publication_cannot_bypass_version_guard(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import app.replay as replay
    publish_records(tmp_path, [record(version="v1")])
    entered, release = Event(), Event()
    original = replay.os.replace
    def paused(source, target):
        entered.set()
        assert release.wait(3)
        return original(source, target)
    monkeypatch.setattr(replay.os, "replace", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publish_records, tmp_path, [record(version="v2")])
        try:
            assert entered.wait(2)
            changed = record(request().model_copy(update={"prompt": "changed"}), version="v1")
            with pytest.raises(GatewayUnavailable) as exc:
                publish_records(tmp_path, [changed])
            assert exc.value.code == "RECORDING_PUBLISH_ACTIVE"
        finally:
            release.set()
        assert future.result(timeout=3)["version"] == "v2"
    assert not (tmp_path / ".publish.lock").exists()
    assert verify_recordings(tmp_path)["record_count"] == 1


def test_published_files_are_checked_against_manifest_before_switch(tmp_path, monkeypatch):
    from pathlib import Path
    publish_records(tmp_path, [record(version="v1")])
    before = (tmp_path / "active.json").read_bytes()
    original = Path.write_text
    def tampered(path, data, *args, **kwargs):
        if path.suffix == ".json" and len(path.stem) == 64:
            value = json.loads(data)
            value["metadata"]["origin"] = "model"
            data = json.dumps(value)
        return original(path, data, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", tampered)
    with pytest.raises(GatewayUnavailable) as exc:
        publish_records(tmp_path, [record(version="v2")])
    assert exc.value.code == "RECORDING_ARTIFACT_MISMATCH"
    assert (tmp_path / "active.json").read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("request_hash", "c" * 64), ("normalization_hash", "b" * 64),
    ("prompt_hash", "c" * 64), ("parameters", {"temperature": 1}),
])
def test_capture_does_not_relabel_mismatched_upstream_metadata(tmp_path, field, value):
    class Inner:
        def generate(self, req, schema):
            item = record(req)
            return GatewayResult(value=ExtractedNeeds(needs=[]), metadata=item.metadata.model_copy(update={"origin": "model", field: value}))
    gateway = RecordingGateway(Inner(), "synthetic-no-live-model", "a" * 64, "capture-test")
    with pytest.raises(GatewayUnavailable) as exc:
        gateway.generate(request(), ExtractedNeeds)
    assert exc.value.code == "RECORD_PROVENANCE_CONFLICT"
    assert gateway.captured == [] and not (tmp_path / "active.json").exists()


def test_schema_and_metadata_hashes_are_verified(tmp_path):
    item = record()
    bad = item.model_copy(update={"metadata": item.metadata.model_copy(update={"request_hash": "f" * 64})})
    with pytest.raises(GatewayUnavailable) as exc:
        publish_records(tmp_path, [bad])
    assert exc.value.code == "REPLAY_RECORD_INVALID"


def test_path_escape_in_active_pointer_is_rejected(tmp_path):
    (tmp_path / "active.json").write_text('{"directory":"../../outside"}')
    with pytest.raises(GatewayUnavailable) as exc:
        verify_recordings(tmp_path)
    assert exc.value.code == "REPLAY_SET_INVALID"


def test_recording_wrapper_refuses_authored_as_model_and_publishes_only_explicitly(tmp_path):
    class Inner:
        origin = "authored"
        def generate(self, req, schema):
            r = record(req)
            return GatewayResult(value=ExtractedNeeds(needs=[]), metadata=r.metadata.model_copy(update={"origin": self.origin}))
    inner = Inner()
    gateway = RecordingGateway(inner, "synthetic-no-live-model", "a" * 64, "capture-test")
    with pytest.raises(GatewayUnavailable) as exc:
        gateway.generate(request(), ExtractedNeeds)
    assert exc.value.code == "RECORD_ORIGIN_INVALID"
    assert not (tmp_path / "active.json").exists()
    # В тесте это имитация транспорта; не измерение настоящей модели.
    inner.origin = "model"
    gateway = RecordingGateway(inner, "synthetic-no-live-model", "a" * 64, "capture-test")
    assert gateway.generate(request(), ExtractedNeeds).metadata.origin == "model"
    assert not (tmp_path / "active.json").exists()
    with pytest.raises(GatewayUnavailable) as exc:
        gateway.publish(tmp_path)
    assert exc.value.code == "RECORD_SCENARIO_INCOMPLETE"
    gateway.publish(tmp_path, scenario_succeeded=True)
    assert verify_recordings(tmp_path)["origins"] == ["model"]


def test_failed_capture_cannot_publish_partial_set(tmp_path):
    publish_records(tmp_path, [record()])
    before = (tmp_path / "active.json").read_bytes()
    class Inner:
        fail = False
        def generate(self, req, schema):
            if self.fail:
                raise RuntimeError("Transport failed")
            r = record(req)
            return GatewayResult(value=ExtractedNeeds(needs=[]), metadata=r.metadata.model_copy(update={"origin": "model"}))
    inner = Inner()
    gateway = RecordingGateway(inner, "synthetic-no-live-model", "a" * 64, "capture-test")
    gateway.generate(request(), ExtractedNeeds)
    inner.fail = True
    with pytest.raises(RuntimeError): gateway.generate(request(), ExtractedNeeds)
    with pytest.raises(GatewayUnavailable) as exc:
        gateway.publish(tmp_path, scenario_succeeded=True)
    assert exc.value.code == "RECORD_SESSION_FAILED"
    assert (tmp_path / "active.json").read_bytes() == before


def test_health_describes_replay_and_live_separately(settings, tmp_path):
    publish_records(tmp_path, [record()])
    configured = settings.model_copy(update={"app_recordings_root": tmp_path, "app_replay_model_id": "synthetic-no-live-model",
        "app_replay_normalization_hash": "a" * 64})
    health = model_health(configured)
    assert health.replay_origins == ["authored"] and health.replay_record_count == 1
    assert health.available_models["text"].code == "REPLAY_READY"
    assert isinstance(text_gateway(configured), ReplayGateway)
    with pytest.raises(GatewayUnavailable) as exc:
        UnavailableGateways(configured).generate(request(), ExtractedNeeds)
    assert exc.value.code == "GATEWAY_NOT_BOUND"
    live = model_health(configured.model_copy(update={"app_model_mode": "live"}))
    assert live.replay_record_count == 0 and live.available_models["text"].code == "MODEL_NOT_CONFIGURED"
