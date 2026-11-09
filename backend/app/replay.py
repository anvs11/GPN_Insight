"""Точное текстовое воспроизведение и публикация проверенных наборов без сети."""

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError

from .gateways import GatewayResult, GatewayUnavailable, TextGateway, TextRequest
from .assessment_schemas import NeedAssessment
from .schemas import (
    BatchAssignment,
    Contract,
    ExtractedNeeds,
    ProposedTopic,
    RecordingMeta,
    Sha256,
    Text,
)

RESPONSE_SCHEMAS = {
    "classify_need": NeedAssessment,
    "extract_needs": ExtractedNeeds,
    "assign_topic": BatchAssignment,
    "propose_topic": ProposedTopic,
}

SCHEMAS = {
    schema.__name__: schema
    for schema in RESPONSE_SCHEMAS.values()
}

NAMESPACE = UUID("4ca17d64-e9aa-529a-8fa4-7d5bd760f4aa")


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def semantic_hash(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def stable_id(kind: str, *parts) -> str:
    return str(uuid5(NAMESPACE, canonical([kind, *parts])))


def request_key(request: TextRequest, response_schema, model_id: str, normalization_hash: str) -> str:
    return semantic_hash({"format": 1, "request": request.model_dump(mode="json"), "model_id": model_id,
                          "normalization_hash": normalization_hash, "response_schema": response_schema.model_json_schema()})


class TextRecording(Contract):
    request: TextRequest
    schema_name: Text
    schema_hash: Sha256
    response: dict
    metadata: RecordingMeta


def validate_record(record: TextRecording):
    schema = SCHEMAS.get(record.schema_name)
    expected = RESPONSE_SCHEMAS.get(record.request.operation)

    if expected is None:
        raise GatewayUnavailable(
            "REPLAY_OPERATION_UNSUPPORTED",
            "Неизвестная операция в записи replay.",
        )

    if (
        schema is not expected
        or semantic_hash(schema.model_json_schema()) != record.schema_hash
    ):
        raise GatewayUnavailable(
            "REPLAY_SCHEMA_MISMATCH",
            "Схема записи изменилась. Пересоздайте проверенный набор.",
        )

    meta = record.metadata

    if (
        meta.request_hash
        != request_key(
            record.request,
            schema,
            meta.model_id,
            meta.normalization_hash,
        )
        or meta.prompt_hash
        != hashlib.sha256(record.request.prompt.encode("utf-8")).hexdigest()
        or meta.parameters != record.request.parameters
    ):
        raise GatewayUnavailable(
            "REPLAY_RECORD_INVALID",
            "Хеши или параметры записи не совпадают. Проверьте набор.",
        )

    value = schema.model_validate(record.response)
    canonical(value.model_dump(mode="json"))
    return value


def make_record(request: TextRequest, response_schema, response, *, model_id: str, normalization_hash: str,
                set_version: str, origin: str) -> TextRecording:
    value = response_schema.model_validate(response)
    record = TextRecording(request=request, schema_name=response_schema.__name__, schema_hash=semantic_hash(response_schema.model_json_schema()),
        response=value.model_dump(mode="json"), metadata=RecordingMeta(
            request_hash=request_key(request, response_schema, model_id, normalization_hash),
            prompt_hash=hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(), normalization_hash=normalization_hash,
            model_id=model_id, parameters=request.parameters, origin=origin, recording_set_version=set_version))
    validate_record(record)
    return record


def _contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("Invalid recording path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Recording path escapes root")
    return path


def _read_json(path: Path, limit: int):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Recording file exceeds limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("Nonfinite JSON value")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject_constant)


class ReplayGateway:
    def __init__(self, root: Path, model_id: str, normalization_hash: str):
        self.root = root
        self.model_id = model_id
        self.normalization_hash = normalization_hash

    def _index(self, pointer_name="active.json"):
        pointer = _read_json(_contained(self.root, pointer_name), 4096)
        folder = _contained(self.root, pointer["directory"])
        manifest = _read_json(_contained(folder, "manifest.json"), 2 * 1024 * 1024)
        if type(manifest["format"]) is not int or manifest["format"] != 1 or not isinstance(manifest["records"], dict) or len(manifest["records"]) > 10000:
            raise ValueError("Invalid manifest")
        if any(re.fullmatch(r"[0-9a-f]{64}", key) is None for key in manifest["records"]):
            raise ValueError("Invalid hash")
        return folder, manifest

    def _record(self, folder, manifest, key):
        record = TextRecording.model_validate(_read_json(_contained(folder, key + ".json"), 10 * 1024 * 1024))
        validate_record(record)
        if semantic_hash(record.model_dump(mode="json")) != manifest["records"][key]:
            raise ValueError("Recording checksum mismatch")
        if key != record.metadata.request_hash or record.metadata.recording_set_version != manifest["version"]:
            raise ValueError("Invalid recording identity")
        return record

    def records(self) -> dict[str, TextRecording]:
        try:
            folder, manifest = self._index()
            return {key: self._record(folder, manifest, key) for key in manifest["records"]}
        except GatewayUnavailable:
            raise
        except (OSError, ValueError, KeyError, TypeError, RecursionError, ValidationError) as exc:
            raise GatewayUnavailable("REPLAY_SET_INVALID", "Набор replay отсутствует или повреждён. Проверьте записи и конфигурацию.") from exc

    def generate(self, request: TextRequest, response_schema):
        try:
            key = request_key(request, response_schema, self.model_id, self.normalization_hash)
        except (ValueError, ValidationError) as exc:
            raise GatewayUnavailable("REPLAY_REQUEST_INVALID", "Запрос не соответствует контракту replay.") from exc
        try:
            folder, manifest = self._index()
            if key not in manifest["records"]:
                raise GatewayUnavailable("REPLAY_RECORD_MISSING", "Точной записи запроса нет. Создайте проверенную запись; live автоматически не вызывается.")
            record = self._record(folder, manifest, key)
            if record.schema_name != response_schema.__name__ or record.schema_hash != semantic_hash(response_schema.model_json_schema()):
                raise GatewayUnavailable("REPLAY_SCHEMA_MISMATCH", "Схема ответа не совпала с записью.")
            return GatewayResult(value=response_schema.model_validate(record.response), metadata=record.metadata)
        except (OSError, ValueError, KeyError, TypeError, RecursionError, ValidationError) as exc:
            raise GatewayUnavailable("REPLAY_SET_INVALID", "Набор replay повреждён. Проверьте сохранённые записи.") from exc


def publish_records(root: Path, records: list[TextRecording]) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".publish.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GatewayUnavailable("RECORDING_PUBLISH_ACTIVE", "Набор уже публикуется или осталась блокировка прерванной записи. Проверьте процесс либо используйте новый каталог.") from exc
    try:
        return _publish_records(root, records)
    finally:
        os.close(descriptor)
        lock.unlink()


def _publish_records(root: Path, records: list[TextRecording]) -> dict:
    if not records:
        raise ValueError("Empty recording set")
    unique = {}
    versions = set()
    for record in records:
        validate_record(record)
        key = record.metadata.request_hash
        versions.add(record.metadata.recording_set_version)
        if key in unique and unique[key] != record:
            raise ValueError("Different responses for identical request")
        unique[key] = record
    if len(versions) != 1:
        raise ValueError("Mixed set versions")
    checksums = {k: semantic_hash(unique[k].model_dump(mode="json")) for k in sorted(unique)}
    history = list(root.glob("active.backup.*.json"))
    if (root / "active.json").exists():
        ReplayGateway(root, "verify", "0" * 64).records()
        history.append(root / "active.json")
    try:
        for pointer in history:
            _, old = ReplayGateway(root, "verify", "0" * 64)._index(pointer.name)
            if old["version"] in versions and old["records"] != checksums:
                raise GatewayUnavailable("RECORDING_VERSION_CONFLICT", "Эта версия уже публиковалась с другим содержимым. Задайте новую версию.")
    except (OSError, ValueError, KeyError, TypeError, RecursionError, ValidationError) as exc:
        raise GatewayUnavailable("RECORDING_HISTORY_INVALID", "История наборов повреждена. Проверьте её; активный набор не менялся.") from exc
    directory = "sets/" + uuid4().hex
    folder = root / directory
    folder.mkdir(parents=True)
    for key, record in unique.items():
        (folder / (key + ".json")).write_text(record.model_dump_json(indent=2), encoding="utf-8")
    manifest = {"format": 1, "version": next(iter(versions)), "records": checksums}
    (folder / "manifest.json").write_text(canonical(manifest), encoding="utf-8")
    # Проверяем весь опубликованный контент до переключения указателя.
    for key in manifest["records"]:
        written = TextRecording.model_validate_json((folder / (key + ".json")).read_text(encoding="utf-8"))
        validate_record(written)
        if semantic_hash(written.model_dump(mode="json")) != checksums[key]:
            raise GatewayUnavailable("RECORDING_ARTIFACT_MISMATCH", "Записанный артефакт не совпал с manifest. Активный набор не менялся.")
    pointer = root / "active.json"
    if pointer.exists():
        (root / ("active.backup." + uuid4().hex + ".json")).write_bytes(pointer.read_bytes())
    pending = root / (".active." + uuid4().hex + ".tmp")
    pending.write_text(canonical({"directory": directory}), encoding="utf-8")
    os.replace(pending, pointer)
    return {"record_count": len(unique), "origins": sorted({r.metadata.origin for r in records}), "version": manifest["version"]}


class RecordingGateway:
    """Оборачивает будущий реальный TextGateway; публикация после успеха сценария."""
    def __init__(self, inner: TextGateway, model_id: str, normalization_hash: str, set_version: str):
        self.inner = inner
        self.model_id = model_id
        self.normalization_hash = normalization_hash
        self.set_version = set_version
        self.captured: list[TextRecording] = []
        self.failed = False

    def generate(self, request: TextRequest, response_schema):
        if self.failed:
            raise GatewayUnavailable("RECORD_SESSION_FAILED", "Сессия записи уже завершилась ошибкой. Начните новый набор.")
        try:
            result = self.inner.generate(request, response_schema)
            if result.metadata.origin != "model" or result.metadata.model_id != self.model_id:
                raise GatewayUnavailable("RECORD_ORIGIN_INVALID", "Live-запись требует ответа реального адаптера с origin=model.")
            meta = result.metadata
            if (meta.request_hash != request_key(request, response_schema, self.model_id, self.normalization_hash) or
                meta.normalization_hash != self.normalization_hash or meta.parameters != request.parameters or
                meta.prompt_hash != hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()):
                raise GatewayUnavailable("RECORD_PROVENANCE_CONFLICT", "Metadata адаптера не соответствуют запросу. Набор не публикуется.")
            record = make_record(request, response_schema, result.value.model_dump(mode="json"), model_id=self.model_id,
                normalization_hash=self.normalization_hash, set_version=self.set_version, origin="model")
            self.captured.append(record)
            return GatewayResult(value=response_schema.model_validate(record.response), metadata=record.metadata)
        except BaseException:
            self.failed = True
            raise

    def publish(self, root: Path, *, scenario_succeeded: bool = False) -> dict:
        if self.failed:
            raise GatewayUnavailable("RECORD_SESSION_FAILED", "Сессия записи содержит ошибку; активный набор не менялся.")
        if scenario_succeeded is not True:
            raise GatewayUnavailable("RECORD_SCENARIO_INCOMPLETE", "Публикация требует успешного завершения всего сценария.")
        return publish_records(root, self.captured)


def verify_recordings(root: Path, requests: list[dict] | None = None) -> dict:
    gateway = ReplayGateway(root, "verify", "0" * 64)
    records = gateway.records()
    if not records:
        raise GatewayUnavailable("REPLAY_SET_EMPTY", "Набор replay пуст.")
    for entry in requests or []:
        key = request_key(TextRequest.model_validate(entry["request"]), SCHEMAS[entry["schema_name"]], entry["model_id"], entry["normalization_hash"])
        if key not in records:
            raise GatewayUnavailable("REPLAY_RECORD_MISSING", "Точной записи проверяемого запроса нет; сеть не вызывается.")
    report = {"record_count": len(records), "origins": sorted({r.metadata.origin for r in records.values()}), "network_used": False}
    if requests is not None:
        report["verified_requests"] = len(requests)
    return report
