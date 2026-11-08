import json
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app import imports_api
from app.config import REPO_ROOT
from app.main import create_app
from app.repositories import Repository
from app.schemas import Run
from app.storage import now


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as value:
        yield value


@pytest.fixture
def manifest():
    return (REPO_ROOT / "data/demo/manifest.json").read_bytes()


def upload(client, manifest, *, preview=False, extra=()):
    return client.post("/api/v1/imports" + ("?dry_run=true" if preview else ""),
        files=[("manifest", ("manifest.json", manifest, "application/json")), *extra])


def multipart(parts, *, closed=True):
    data = bytearray()
    for name, value, filename in parts:
        data.extend(b"--synthetic-boundary\r\nContent-Disposition: form-data; name=\"" + name + b'"')
        if filename is not None:
            data.extend(b'; filename="' + filename + b'"')
        data.extend(b"\r\n\r\n" + value + b"\r\n")
    if closed:
        data.extend(b"--synthetic-boundary--\r\n")
    return bytes(data)


HEADERS = {"Content-Type": "multipart/form-data; boundary=synthetic-boundary"}


def test_preview_commit_repeat_history_and_readiness_are_independent(client, manifest):
    path = client.app.state.repository.path
    before = path.read_bytes()
    assert client.get("/api/v1/imports").json() == []
    preview = upload(client, manifest, preview=True)
    assert preview.status_code == 200 and preview.json()["can_commit"] is True
    assert preview.json()["counts"]["inserted"] == 4
    assert path.read_bytes() == before and client.get("/api/v1/imports").json() == []
    first = upload(client, manifest)
    assert first.status_code == 200 and first.json()["counts"]["inserted"] == 4
    repeated = upload(client, manifest)
    assert repeated.status_code == 200 and repeated.json()["counts"]["unchanged"] == 4
    history = client.get("/api/v1/imports").json()
    assert len(history) == 2 and history[0]["registration_id"] != history[1]["registration_id"]
    assert all(entry["synthetic"] is True and entry["product_id"] == "webneft" for entry in history)
    assert client.post("/api/v1/runs", json={}).json()["code"] == "INTEGRATION_NOT_READY"
    assert client.app.state.repository.active_run() is None
    assert all(client.app.state.repository.get_source(t["source_id"]).source_version == 1 for t in json.loads(manifest)["tickets"])


def test_commit_revalidates_current_data_without_preview_session(client, manifest):
    assert upload(client, manifest, preview=True).json()["counts"]["inserted"] == 4
    partial = json.loads(manifest)
    partial["tickets"] = partial["tickets"][:1]
    assert upload(client, json.dumps(partial)).json()["counts"]["inserted"] == 1
    committed = upload(client, manifest)
    assert committed.json()["counts"] == {"inserted": 3, "updated": 0, "unchanged": 1, "invalid": 0}
    bad = json.loads(manifest)
    bad["synthetic"] = "true"
    assert upload(client, json.dumps(bad)).status_code == 422
    assert len(client.get("/api/v1/imports").json()) == 2


@pytest.mark.parametrize("preview", [True, False], ids=["preview", "commit"])
@pytest.mark.parametrize("kind", ["no-manifest", "duplicate", "unknown", "files", "two-files", "incomplete", "wrong-type", "utf8", "json", "media"],
                         ids=["missing", "duplicate", "unknown", "file", "repeat-files", "incomplete", "json-content-type", "bad-utf8", "bad-json", "manifest-media"])
def test_invalid_input_never_partially_imports_or_echoes_input(client, manifest, preview, kind, caplog):
    raw = manifest
    parts = [(b"manifest", raw, b"manifest.json")]
    headers = HEADERS
    if kind == "no-manifest": parts = []
    elif kind == "duplicate": parts *= 2
    elif kind == "unknown": parts.append((b"SENSITIVE_SENTINEL", b"private", None))
    elif kind in {"files", "two-files"}:
        parts += [(b"files", b"SENSITIVE_SENTINEL", b"../../private.wav")] * (2 if kind == "two-files" else 1)
    elif kind == "wrong-type": headers = {"Content-Type": "application/json"}
    elif kind == "utf8": parts = [(b"manifest", b"\xffSENSITIVE_SENTINEL", None)]
    elif kind == "json": parts = [(b"manifest", b'{"SENSITIVE_SENTINEL":', None)]
    elif kind == "media":
        data = json.loads(manifest)
        data["tickets"][0]["assets"] = [{"asset_id": "a-1", "kind": "audio", "path": "synthetic.wav", "mime_type": "audio/wav"}]
        parts = [(b"manifest", json.dumps(data).encode(), None)]
    body = multipart(parts, closed=kind != "incomplete")
    before = client.app.state.repository.path.read_bytes()
    response = client.post("/api/v1/imports" + ("?dry_run=true" if preview else ""), headers=headers, content=body)
    assert response.status_code == (200 if preview else 422)
    if preview:
        assert response.json()["status"] == "invalid" and response.json()["can_commit"] is False
    assert "SENSITIVE_SENTINEL" not in response.text and "SENSITIVE_SENTINEL" not in caplog.text
    assert client.app.state.repository.path.read_bytes() == before
    assert client.get("/api/v1/imports").json() == []


@pytest.mark.parametrize("kind", ["body", "manifest", "part", "headers", "parts", "fields", "files"])
@pytest.mark.parametrize("preview", [True, False], ids=["preview", "commit"])
def test_size_limits_are_413_in_both_modes(client, manifest, kind, preview, monkeypatch):
    parts = [(b"manifest", manifest, b"manifest.json")]
    if kind == "body":
        body = b"x" * (imports_api.MAX_BODY_BYTES + 1)
        monkeypatch.setattr(imports_api, "MultipartParser", lambda *a, **k: pytest.fail("Oversized body reached parser"))
    else:
        if kind == "manifest": parts = [(b"manifest", b"x" * (imports_api.MAX_MANIFEST_BYTES + 1), None)]
        elif kind == "part": parts.append((b"files", b"x" * (imports_api.MAX_PART_BYTES + 1), b"synthetic.wav"))
        elif kind == "headers": parts = [(b"manifest", b"{}", b"x" * (imports_api.MAX_PART_HEADER_BYTES + 1))]
        elif kind in {"parts", "fields", "files"}:
            count = {"parts": imports_api.MAX_PARTS, "fields": imports_api.MAX_FIELDS, "files": imports_api.MAX_FILES}[kind] + 1
            if kind == "files": monkeypatch.setattr(imports_api, "MAX_PARTS", count + 1)
            parts = [(b"manifest", b"{}", None if kind == "fields" else b"a.json")] * count
        body = multipart(parts)
    before = client.app.state.repository.path.read_bytes()
    response = client.post("/api/v1/imports" + ("?dry_run=true" if preview else ""), headers=HEADERS, content=body)
    assert response.status_code == 413
    assert client.app.state.repository.path.read_bytes() == before
    assert client.get("/api/v1/imports").json() == []


@pytest.mark.parametrize("length", [None, "1", "999999999"], ids=["missing", "understated", "overstated"])
def test_content_length_is_not_the_source_of_truth(client, manifest, length):
    body = multipart([(b"manifest", manifest, None)])
    headers = dict(HEADERS)
    if length is not None: headers["Content-Length"] = length
    response = client.post("/api/v1/imports?dry_run=true", headers=headers,
        content=iter(body[i:i + 31] for i in range(0, len(body), 31)))
    assert response.status_code == 200 and response.json()["can_commit"]
    too_large = client.post("/api/v1/imports", headers={**HEADERS, "Content-Length": "1"},
        content=iter([b"x" * imports_api.MAX_BODY_BYTES, b"x"]))
    assert too_large.status_code == 413


def test_exact_manifest_limit_and_utf8_field_work_without_spooling(client, manifest, monkeypatch):
    import starlette.formparsers as forms
    import app.main as main
    import app.gateways as gateways
    def forbidden(*args, **kwargs): pytest.fail("Import invoked startup/model or spooled a file")
    monkeypatch.setattr(forms, "SpooledTemporaryFile", forbidden)
    monkeypatch.setattr(main, "initialize", forbidden)
    for method in ["generate", "transcribe", "describe"]:
        monkeypatch.setattr(gateways.UnavailableGateways, method, forbidden)
    padded = manifest + b" " * (imports_api.MAX_MANIFEST_BYTES - len(manifest))
    assert upload(client, padded, preview=True).json()["can_commit"]
    body = multipart([(b"manifest", manifest, None)])
    assert client.post("/api/v1/imports", headers=HEADERS, content=body).status_code == 200


def test_run_active_blocks_commit_but_does_not_reserve_preview(client, manifest):
    repo = client.app.state.repository
    repo.create_run(Run(run_id="synthetic-active", mode="replay", state="queued", stage="queued", created_at=now()))
    before = repo.path.read_bytes()
    assert upload(client, manifest, preview=True).json()["can_commit"]
    response = upload(client, manifest)
    assert response.status_code == 409 and response.json()["code"] == "RUN_ACTIVE"
    assert repo.path.read_bytes() == before and repo.list_imports() == []


def test_import_active_is_visible_when_starting_run(client, manifest, monkeypatch):
    entered, release = Event(), Event()
    original = Repository.register_dataset
    def paused(self, batch, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, batch, **kwargs)
    monkeypatch.setattr(Repository, "register_dataset", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, client, manifest)
        try:
            assert entered.wait(3)
            response = client.post("/api/v1/runs", json={})
            assert response.status_code == 409 and response.json()["code"] == "IMPORT_ACTIVE"
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200
    assert client.app.state.repository.active_run() is None


@pytest.mark.parametrize("query", ["?dry_run=yes", "?dry_run=true&dry_run=false", "?product_id=other", "?dry_run=true&registration_id=client-id"])
def test_control_parameters_cannot_be_smuggled(client, manifest, query):
    response = client.post("/api/v1/imports" + query, files={"manifest": ("manifest.json", manifest)})
    assert response.status_code == 422
    assert client.get("/api/v1/imports?product_id=other").status_code == 422
    assert client.get("/api/v1/imports").json() == []


def test_openapi_declares_existing_models_and_multipart(client):
    schema = client.get("/openapi.json").json()
    route = schema["paths"]["/api/v1/imports"]
    assert "multipart/form-data" in route["post"]["requestBody"]["content"]
    names = {x["$ref"].rsplit("/", 1)[1] for x in route["post"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]}
    assert names == {"ImportPreview", "ImportResult"}
    assert route["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/ImportLogEntry")


@pytest.mark.parametrize("chunk_size", [1, 7, 32768], ids=["byte", "small", "normal"])
def test_parser_chunk_boundaries_preserve_utf8_and_headers(client, manifest, monkeypatch, chunk_size):
    monkeypatch.setattr(imports_api, "READ_CHUNK_BYTES", chunk_size)
    response = upload(client, manifest, preview=True)
    assert response.status_code == 200 and response.json()["counts"]["inserted"] == 4


def test_actual_receive_limit_stops_before_reading_more_or_creating_parser():
    import asyncio
    class RequestStream:
        async def stream(self):
            yield b"x" * imports_api.MAX_BODY_BYTES
            yield b"x"
            pytest.fail("Read continued after body limit")
    with pytest.raises(imports_api.ImportRequestError) as exc:
        asyncio.run(imports_api._read_body(RequestStream(), BytesIO()))
    assert exc.value.status_code == 413


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_database_error_is_not_an_empty_database_or_auto_init(client, manifest, tmp_path, monkeypatch, state):
    repo = client.app.state.repository
    other = tmp_path / (state + ".db")
    if state == "corrupt": other.write_bytes(b"synthetic invalid database")
    with monkeypatch.context() as m:
        m.setattr(repo, "path", other)
        preview = upload(client, manifest, preview=True)
        assert preview.status_code == 200 and preview.json()["can_commit"] is False
        assert preview.json()["counts"]["inserted"] == 0 and preview.json()["errors"]
        response = upload(client, manifest)
        assert response.status_code == 503
        if state == "missing": assert not other.exists()
        else: assert other.read_bytes() == b"synthetic invalid database"


@pytest.mark.parametrize("preview", [True, False], ids=["preview", "commit"])
def test_parser_failure_is_safe_and_does_not_log_received_bytes(client, caplog, preview):
    response = client.post("/api/v1/imports" + ("?dry_run=true" if preview else ""),
        headers=HEADERS, content=b"--SENSITIVE_SENTINEL\r\n")
    assert response.status_code == (200 if preview else 422)
    assert "SENSITIVE_SENTINEL" not in response.text
    assert not any(record.name.startswith("python_multipart") for record in caplog.records)
    assert client.get("/api/v1/imports").json() == []


def test_disconnected_stream_never_reaches_multipart_or_commit():
    import asyncio
    from starlette.requests import ClientDisconnect
    class RequestStream:
        async def stream(self):
            yield b"--synthetic-boundary\r\n"
            raise ClientDisconnect()
    with pytest.raises(imports_api.ImportRequestError) as exc:
        asyncio.run(imports_api._read_body(RequestStream(), BytesIO()))
    assert exc.value.code == "IMPORT_BODY_INCOMPLETE" and exc.value.status_code == 422


def test_database_failure_before_commit_is_503_and_rolls_back_everything(client, manifest, monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    original = Repository._session
    before = client.app.state.repository.path.read_bytes()
    @contextmanager
    def fail(self, write=False):
        with original(self, write=write) as db:
            yield db
            # _register_dataset commits before returning: fail while its real
            # DB session is still open, not after an already successful commit.
            if write:
                raise sqlite3.OperationalError("SENSITIVE_SENTINEL")
    with monkeypatch.context() as m:
        m.setattr(Repository, "_session", fail)
        response = upload(client, manifest)
        assert response.status_code == 503 and response.json()["code"] == "IMPORT_DATABASE_UNAVAILABLE"
        assert "SENSITIVE_SENTINEL" not in response.text
    assert client.app.state.repository.path.read_bytes() == before
    assert client.get("/api/v1/imports").json() == []
    assert upload(client, manifest).json()["counts"]["inserted"] == 4


def test_lost_response_after_commit_is_not_a_rollback(client, manifest, monkeypatch):
    from app.repositories import RepositoryError
    original = imports_api.import_tickets
    def lose_response(*args, **kwargs):
        original(*args, **kwargs)
        raise RepositoryError("SYNTHETIC_RESPONSE_LOST", "Синтетическая потеря ответа после commit.", 503)
    with monkeypatch.context() as m:
        m.setattr(imports_api, "import_tickets", lose_response)
        assert upload(client, manifest).status_code == 503
    assert len(client.get("/api/v1/imports").json()) == 1
    repeated = upload(client, manifest)
    assert repeated.status_code == 200 and repeated.json()["counts"]["unchanged"] == 4
    assert len(client.get("/api/v1/imports").json()) == 2


def test_synthetic_http_examples_use_existing_contracts_and_current_limits():
    from app.schemas import ErrorResponse, ImportLogEntry, ImportPreview, ImportResult
    data = json.loads((REPO_ROOT / "data/contracts/import-http-examples.json").read_text(encoding="utf-8"))
    assert data["synthetic"] is True and data["origin"] == "authored"
    assert data["purpose"] == "import_http_contract_examples_only"
    assert data["limits"]["body_bytes"] == imports_api.MAX_BODY_BYTES
    for name, response in data["responses"].items():
        if name in {"valid_preview", "invalid_preview"}:
            ImportPreview.model_validate(response["body"])
        elif name in {"commit", "repeat"}:
            ImportResult.model_validate(response["body"])
        elif name == "history":
            assert all(ImportLogEntry.model_validate(entry).synthetic for entry in response["body"])
        else:
            ErrorResponse.model_validate(response["body"])
