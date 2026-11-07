"""Запись/публикация целого сценария; verify всегда использует strict replay."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

from .config import REPO_ROOT
from .gateways import GatewayUnavailable
from .replay import ReplayGateway, _contained, _read_json, canonical, publish_records, semantic_hash
from .scenario import load_scenario, require, run_flow, scenario_profile


def _workspace() -> Path:
    folder = REPO_ROOT / "var" / "scenario-runs" / uuid4().hex
    folder.mkdir(parents=True)
    return folder


def _write(path, value):
    path.write_text(canonical(value), encoding="utf-8")


def _file_hash(path):
    from hashlib import sha256
    result, size = sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(64 * 1024):
            size += len(block)
            require(size <= 10 * 1024 * 1024, "SCENARIO_ARTIFACT_TOO_LARGE")
            result.update(block)
    return result.hexdigest()


def _artifact(folder: Path):
    try:
        manifest = _read_json(_contained(folder, "manifest.json"), 2 * 1024 * 1024)
        require(manifest["format"] == 1 and isinstance(manifest["files"], dict) and 0 < len(manifest["files"]) <= 1000,
                "SCENARIO_SET_INVALID")
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
        require(actual == set(manifest["files"]) | {"manifest.json"}, "SCENARIO_SET_INVALID")
        for name, expected in manifest["files"].items():
            require(_file_hash(_contained(folder, name)) == expected, "SCENARIO_CHECKSUM_MISMATCH")
        spec, snapshot = load_scenario(_contained(folder, "scenario.json"))
        profile = _read_json(_contained(folder, "profile.json"), 512 * 1024)
        trace = _read_json(_contained(folder, "trace.json"), 10 * 1024 * 1024)
        gateway = ReplayGateway(folder / "recordings", spec.model_id, profile["parameters"]["normalization_hash"])
        records = gateway.records()
        require(records and all(r.metadata.recording_set_version == spec.recording_set_version for r in records.values()),
                "SCENARIO_SET_INVALID")
        fingerprint = semantic_hash(dict(spec=spec.model_dump(mode="json"), initial_state=snapshot,
            profile=profile, trace=trace, records={key: semantic_hash(r.model_dump(mode="json")) for key, r in sorted(records.items())}))
        require(manifest["fingerprint"] == fingerprint and manifest["version"] == spec.recording_set_version,
                "SCENARIO_CHECKSUM_MISMATCH")
        return spec, profile, trace, gateway, manifest
    except GatewayUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise GatewayUnavailable("SCENARIO_SET_INVALID", "Сценарный набор отсутствует или повреждён. Прежний активный набор сохранён.") from exc


def _index(root: Path, pointer_name="active.json"):
    try:
        pointer = _read_json(_contained(root, pointer_name), 4096)
        folder = _contained(root, pointer["directory"])
        require(pointer["format"] == 1 and _file_hash(folder / "manifest.json") == pointer["manifest_hash"],
                "SCENARIO_CHECKSUM_MISMATCH")
        return folder, _artifact(folder)
    except GatewayUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GatewayUnavailable("SCENARIO_SET_INVALID", "Указатель сценария отсутствует или повреждён; проверьте отдельный набор.") from exc


def _verify(folder: Path, expected_spec, workspace: Path) -> dict:
    spec, stored_profile, expected_trace, gateway, manifest = _artifact(folder)
    require(spec == expected_spec, "SCENARIO_SPEC_CONFLICT")
    _, profile = scenario_profile(spec, workspace / "verify.db")
    require(profile.model_dump(mode="json") == stored_profile, "SCENARIO_PROFILE_CONFLICT")
    # Ни авторского fixture, ни live factory здесь нет.
    trace = run_flow(spec, workspace / "verify.db", gateway, unknown_check=True)
    require(trace == expected_trace, "SCENARIO_TRACE_MISMATCH")
    report = dict(verified=True, synthetic=True, scenario_id=spec.scenario_id,
        scenario_hash=semantic_hash(spec.model_dump(mode="json")), trace_hash=semantic_hash(trace),
        steps=len(trace), record_count=len(gateway.records()), origins=sorted({r.metadata.origin for r in gateway.records().values()}),
        model_calls=False, network_used=False, unknown_request="REPLAY_RECORD_MISSING", fingerprint=manifest["fingerprint"],
        workspace=str(workspace.relative_to(REPO_ROOT)))
    _write(workspace / "result.json", report)
    return report


def verify_scenario(spec_path: Path, root: Path) -> dict:
    spec, _ = load_scenario(spec_path)
    folder, _ = _index(root.resolve())
    return _verify(folder, spec, _workspace())


def _publish(root, artifact):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".scenario-publish.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GatewayUnavailable("SCENARIO_PUBLISH_ACTIVE", "Сценарий уже публикуется или осталась блокировка. Проверьте процесс либо выберите новый каталог.") from exc
    try:
        incoming = _artifact(artifact)[4]
        pointers = list(root.glob("active.backup.*.json"))
        if (root / "active.json").exists():
            pointers.append(root / "active.json")
        for pointer in pointers:
            previous = _index(root, pointer.name)[1][4]
            require(previous["version"] != incoming["version"] or previous["fingerprint"] == incoming["fingerprint"],
                    "SCENARIO_VERSION_CONFLICT")
        directory = "sets/" + uuid4().hex
        target = _contained(root, directory)
        shutil.copytree(artifact, target)
        _artifact(target)  # Полная проверка записанного контента до переключения.
        pointer = root / "active.json"
        if pointer.exists():
            (root / ("active.backup." + uuid4().hex + ".json")).write_bytes(pointer.read_bytes())
        pending = root / (".active." + uuid4().hex + ".tmp")
        _write(pending, dict(format=1, directory=directory, manifest_hash=_file_hash(target / "manifest.json")))
        os.replace(pending, pointer)
    finally:
        os.close(descriptor)
        lock.unlink()


def record_scenario(spec_path: Path, root: Path, *, authored=False) -> dict:
    if authored is not True:
        raise GatewayUnavailable("LIVE_RECORDING_NOT_CONNECTED", "Для сценарного live record нужен проверенный внутренний TextGateway. Сейчас доступна только явно authored синтетическая запись.")
    root = root.resolve()
    require(root.is_relative_to((REPO_ROOT / "var").resolve()) and root != (REPO_ROOT / "var").resolve(),
            "SCENARIO_OUTPUT_NOT_ALLOWED")
    spec, snapshot = load_scenario(spec_path)
    workspace = _workspace()
    artifact = workspace / "artifact"
    artifact.mkdir()
    _, profile = scenario_profile(spec, workspace / "capture.db")
    from scripts.scenario_fixture import ScenarioAuthoredGateway
    gateway = ScenarioAuthoredGateway(spec, profile)
    trace = run_flow(spec, workspace / "capture.db", gateway)
    publish_records(artifact / "recordings", gateway.records)
    _write(artifact / "scenario.json", spec.model_dump(mode="json"))
    _write(artifact / "initial_state.json", snapshot)
    _write(artifact / "profile.json", profile.model_dump(mode="json"))
    _write(artifact / "trace.json", trace)
    records = ReplayGateway(artifact / "recordings", spec.model_id, profile.parameters["normalization_hash"]).records()
    fingerprint = semantic_hash(dict(spec=spec.model_dump(mode="json"), initial_state=snapshot,
        profile=profile.model_dump(mode="json"), trace=trace,
        records={key: semantic_hash(r.model_dump(mode="json")) for key, r in sorted(records.items())}))
    _write(artifact / "manifest.json", dict(format=1, version=spec.recording_set_version, fingerprint=fingerprint,
        files={p.relative_to(artifact).as_posix(): _file_hash(p) for p in sorted(artifact.rglob("*")) if p.is_file()}))
    verify_folder = workspace / "self-verify"
    verify_folder.mkdir()
    report = _verify(artifact, spec, verify_folder)
    _publish(root, artifact)
    return {**report, "published": True, "recording_set_version": spec.recording_set_version}
