import json
import subprocess
import sys
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest

from app.clock import fixed_clock, now
from app.config import REPO_ROOT
from app.gateways import GatewayUnavailable
from app.replay import semantic_hash
from app.scenario import load_scenario, scenario_profile
import app.scenario_recordings as scenario
from app.scenario_recordings import record_scenario, verify_scenario

SPEC = REPO_ROOT / "data/scenarios/text_flow.json"


@pytest.fixture(scope="module")
def recorded():
    from uuid import uuid4
    root = REPO_ROOT / "var/pytest" / uuid4().hex / "scenario"
    report = record_scenario(SPEC, root, authored=True)
    return root, report


def test_full_scenario_record_verify_is_deterministic_and_offline(recorded, monkeypatch):
    root, captured = recorded
    from scripts.scenario_fixture import ScenarioAuthoredGateway
    monkeypatch.setattr(ScenarioAuthoredGateway, "generate", lambda *a, **k: pytest.fail("Authored fallback reached verifier"))
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: pytest.fail("Network reached verifier"))
    verified = verify_scenario(SPEC, root)
    assert captured["trace_hash"] == verified["trace_hash"] and captured["fingerprint"] == verified["fingerprint"]
    assert verified["record_count"] == 11 and verified["steps"] == 9 and verified["origins"] == ["authored"]
    assert verified["unknown_request"] == "REPLAY_RECORD_MISSING" and not verified["network_used"]
    folder, (_, _, trace, gateway, _) = scenario._index(root)
    assert [step["step"] for step in trace] == ["base", "decision", "passport", "repeat", "delta", "review", "exclude", "merge", "restart"]
    assert trace[2]["state"]["topics"] != trace[1]["state"]["topics"]
    # Delta-запрос видит уже изменённый паспорт, решение и новый title.
    requests = [r.request for r in gateway.records().values() if r.request.operation == "assign_topic"
                and r.request.data["need"]["source_id"] == "FLOW-004"]
    assert len(requests) == 1
    assert any(t["passport"]["version"] == 2 and t["passport"]["scope_confirmed"] is True
               and t["status"] == "needs_research" for t in requests[0].data["topics"])
    assert trace[-1]["run"]["reused_steps"] == 13 and trace[-1]["run"]["completed_steps"] == 0
    assert load_scenario(folder / "scenario.json")[0] == load_scenario(SPEC)[0]


def changed_spec(tmp_path, **changes):
    data = json.loads(SPEC.read_text(encoding="utf-8"))
    data.update(changes)
    (tmp_path / "initial_state.json").write_bytes((SPEC.parent / "initial_state.json").read_bytes())
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("change", [dict(clock="2026-11-08T12:00:00Z"), dict(recording_set_version="other-v1"),
    dict(initial_state_hash="f" * 64), dict(model_id="other-model")])
def test_spec_snapshot_and_clock_are_bound(recorded, tmp_path, change):
    root, _ = recorded
    pointer = (root / "active.json").read_bytes()
    with pytest.raises(GatewayUnavailable):
        verify_scenario(changed_spec(tmp_path, **change), root)
    assert (root / "active.json").read_bytes() == pointer


def test_snapshot_must_be_empty_before_any_scenario_executes(tmp_path):
    path = changed_spec(tmp_path)
    (tmp_path / "initial_state.json").write_text('{"state":"working-database"}', encoding="utf-8")
    with pytest.raises(GatewayUnavailable) as exc:
        record_scenario(path, tmp_path / "output", authored=True)
    assert exc.value.code == "SCENARIO_INITIAL_STATE_UNSUPPORTED"
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("filename", ["trace.json", "profile.json", "initial_state.json", "scenario.json", "record"])
def test_corrupted_artifact_is_rejected(recorded, tmp_path, filename):
    import shutil
    root, _ = recorded
    clone = tmp_path / "copy"
    shutil.copytree(root, clone)
    folder, _ = scenario._index(clone)
    target = (next((folder / "recordings/sets").glob("*/*.json")) if filename == "record" else folder / filename)
    target.write_bytes(target.read_bytes() + b' ')
    before = (clone / "active.json").read_bytes()
    with pytest.raises(GatewayUnavailable) as exc:
        verify_scenario(SPEC, clone)
    assert exc.value.code == "SCENARIO_CHECKSUM_MISMATCH"
    assert (clone / "active.json").read_bytes() == before


def test_trace_semantics_are_verified_even_with_recomputed_checksums(recorded, tmp_path):
    import shutil
    root, _ = recorded
    clone = tmp_path / "copy"
    shutil.copytree(root, clone)
    folder, (spec, profile, trace, gateway, manifest) = scenario._index(clone)
    trace = deepcopy(trace)
    trace[-1]["run"]["reused_steps"] = 99
    scenario._write(folder / "trace.json", trace)
    manifest["files"]["trace.json"] = scenario._file_hash(folder / "trace.json")
    manifest["fingerprint"] = semantic_hash(dict(spec=spec.model_dump(mode="json"),
        initial_state=json.loads((folder / "initial_state.json").read_text(encoding="utf-8")), profile=profile, trace=trace,
        records={k: semantic_hash(r.model_dump(mode="json")) for k, r in sorted(gateway.records().items())}))
    scenario._write(folder / "manifest.json", manifest)
    pointer = json.loads((clone / "active.json").read_text(encoding="utf-8"))
    pointer["manifest_hash"] = scenario._file_hash(folder / "manifest.json")
    scenario._write(clone / "active.json", pointer)
    with pytest.raises(GatewayUnavailable) as exc:
        verify_scenario(SPEC, clone)
    assert exc.value.code == "SCENARIO_TRACE_MISMATCH"


def test_failed_record_does_not_switch_active_set(recorded, monkeypatch):
    root, _ = recorded
    before = (root / "active.json").read_bytes()
    def fail(*a, **k):
        raise GatewayUnavailable("SYNTHETIC_FAILURE", "Синтетическая остановка")
    monkeypatch.setattr(scenario, "run_flow", fail)
    with pytest.raises(GatewayUnavailable):
        record_scenario(SPEC, root, authored=True)
    assert (root / "active.json").read_bytes() == before


def test_publication_rechecks_written_bytes_before_pointer_switch(recorded, monkeypatch):
    import shutil
    root, _ = recorded
    before = (root / "active.json").read_bytes()
    original = shutil.copytree
    def corrupt(source, destination, *a, **k):
        result = original(source, destination, *a, **k)
        target = Path(destination) / "trace.json"
        if target.is_file():
            target.write_bytes(target.read_bytes() + b' ')
        return result
    monkeypatch.setattr(scenario.shutil, "copytree", corrupt)
    with pytest.raises(GatewayUnavailable):
        record_scenario(SPEC, root, authored=True)
    assert (root / "active.json").read_bytes() == before
    assert not (root / ".scenario-publish.lock").exists()


def test_same_version_changed_content_cannot_replace_active(recorded, tmp_path):
    root, _ = recorded
    before = (root / "active.json").read_bytes()
    with pytest.raises(GatewayUnavailable) as exc:
        record_scenario(changed_spec(tmp_path, clock="2026-11-08T12:00:00Z"), root, authored=True)
    assert exc.value.code == "SCENARIO_VERSION_CONFLICT"
    assert (root / "active.json").read_bytes() == before


def test_identical_repeat_is_allowed_and_keeps_backup(recorded):
    root, before = recorded
    result = record_scenario(SPEC, root, authored=True)
    assert before["fingerprint"] == result["fingerprint"]
    assert list(root.glob("active.backup.*.json"))


def test_profile_change_is_not_accepted_as_replay(recorded, monkeypatch):
    root, _ = recorded
    def changed(spec, database):
        settings, profile = scenario_profile(spec, database)
        profile.prompt_hashes["assign_topic"] = "f" * 64
        return settings, profile
    monkeypatch.setattr(scenario, "scenario_profile", changed)
    with pytest.raises(GatewayUnavailable) as exc:
        verify_scenario(SPEC, root)
    assert exc.value.code == "SCENARIO_PROFILE_CONFLICT"


def test_clock_scope_restores_real_time_and_does_not_leak_to_other_contexts():
    value = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with fixed_clock(value):
        assert now() == value
        with fixed_clock(datetime(2001, 1, 1, tzinfo=timezone.utc)):
            assert now().year == 2001
        assert now() == value
    assert now().year != 2000


def test_cli_missing_live_gateway_and_unknown_scenario_exit_nonzero(recorded, tmp_path):
    root, _ = recorded
    before = (root / "active.json").read_bytes()
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", "record", "--scenario", str(SPEC),
        "--recordings", str(root)], cwd=REPO_ROOT / "backend", capture_output=True)
    assert result.returncode == 1 and b"LIVE_RECORDING_NOT_CONNECTED" in result.stderr
    changed = changed_spec(tmp_path, clock="2026-11-08T12:00:00Z")
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", "verify-recordings", "--scenario", str(changed),
        "--recordings", str(root)], cwd=REPO_ROOT / "backend", capture_output=True)
    assert result.returncode == 1 and b"SCENARIO_SPEC_CONFLICT" in result.stderr
    assert (root / "active.json").read_bytes() == before


def test_missing_known_flow_request_keeps_explicit_replay_error(recorded, monkeypatch):
    from app.replay import ReplayGateway
    root, _ = recorded
    original = ReplayGateway.generate
    def missing(self, request, response_schema):
        if request.operation == "assign_topic" and request.data["need"]["source_id"] == "FLOW-004":
            raise GatewayUnavailable("REPLAY_RECORD_MISSING", "Синтетически отсутствующий точный запрос")
        return original(self, request, response_schema)
    monkeypatch.setattr(ReplayGateway, "generate", missing)
    with pytest.raises(GatewayUnavailable) as exc:
        verify_scenario(SPEC, root)
    assert exc.value.code == "REPLAY_RECORD_MISSING"
