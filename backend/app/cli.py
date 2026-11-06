import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from . import schemas
from .config import REPO_ROOT, Settings
from .db import initialize
from .demo import load_fixture
from .repositories import Repository, RepositoryError
from .replay import SCHEMAS, make_record, publish_records, verify_recordings
from .gateways import GatewayUnavailable, TextRequest
from .ingestion.importer import MAX_MANIFEST_BYTES, import_tickets

EXAMPLE_SCHEMAS = {
    cls.__name__: cls for cls in (
        schemas.TicketInput, schemas.MaterialInput, schemas.NormalizedDocument,
        schemas.Fragment, schemas.ExtractedNeed, schemas.TopicPassport,
        schemas.TopicAssignment, schemas.BatchAssignment, schemas.ImportPreview,
        schemas.ImportResult, schemas.ReviewItem, schemas.DecisionEvent,
        schemas.DecisionUpdate, schemas.Run, schemas.Topic, schemas.RecordingMeta,
    )
}

STORAGE_SCHEMAS = {cls.__name__: cls for cls in (
    schemas.DatasetRegistration, schemas.RegisteredMaterial, schemas.ImportLogEntry,
    schemas.ManualOverride, schemas.TopicChangeEvent, schemas.CatalogSnapshot,
)}

PIPELINE_SCHEMAS = {cls.__name__: cls for cls in (
    schemas.RunRequest, schemas.ProcessingProfile, schemas.ExtractedNeeds,
    schemas.ExtractionResult, schemas.AssignmentResult, schemas.ProposedTopic,
)}

WORKFLOW_SCHEMAS = {cls.__name__: cls for cls in (
    schemas.ResolveReviewRequest, schemas.MergePreviewRequest, schemas.MergePreview, schemas.MergeTopicsRequest,
    schemas.MergeResult, schemas.ExcludeNeedsRequest, schemas.ExcludeResult,
)}


def validate_examples(path: Path) -> int:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or bundle.get("synthetic") is not True or bundle.get("purpose") != "contract_examples_only":
        raise ValueError("Примеры должны быть помечены как синтетические образцы контрактов")
    examples = bundle.get("examples")
    if not isinstance(examples, list):
        raise ValueError("Нужен массив examples")
    seen = set()
    for entry in examples:
        if not isinstance(entry, dict) or not isinstance(entry.get("schema"), str) or "value" not in entry:
            raise ValueError("Для примера нужны schema и value")
        name = entry["schema"]
        if name not in EXAMPLE_SCHEMAS:
            raise ValueError("Неизвестная схема примера")
        EXAMPLE_SCHEMAS[name].model_validate(entry["value"])
        seen.add(name)
    if seen != EXAMPLE_SCHEMAS.keys():
        raise ValueError("Не для всех контрактов есть примеры")
    return len(examples)


def validate_http_examples(path: Path) -> int:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("synthetic") is not True or bundle.get("purpose") != "http_contract_examples_only":
        raise ValueError("HTTP-примеры должны быть явно синтетическими")
    expected = {
        "empty_topics": (200, None), "topic": (200, schemas.TopicDetail),
        "source": (200, schemas.SourceDetail),
        "version_conflict": (409, schemas.ErrorResponse),
        "validation_error": (422, schemas.ErrorResponse),
    }
    responses = bundle["responses"]
    if responses.keys() != expected.keys():
        raise ValueError("Нужны примеры всех согласованных HTTP-ответов")
    for name, (status_code, model) in expected.items():
        example = responses[name]
        if example["status_code"] != status_code:
            raise ValueError("Неверный HTTP-статус примера")
        if model is None:
            if example["body"] != []:
                raise ValueError("Пустая база должна возвращать пустой список")
        else:
            value = model.model_validate(example["body"])
            if name in {"topic", "source"} and (not value.synthetic or value.data_origin != "authored"):
                raise ValueError("Примеры ответов должны сохранять synthetic/authored")
    for name in ("decision.json", "decision-new.json"):
        schemas.DecisionUpdate.model_validate_json((path.parent / name).read_text(encoding="utf-8"))
    return len(expected) + 2


def main() -> int:
    parser = argparse.ArgumentParser(description="GPN Insight — команды backend")
    parser.add_argument("command", choices=["init-db", "seed-demo", "validate-examples", "export-schemas", "record", "verify-recordings", "import", "report"])
    parser.add_argument("--date-from", type=date.fromisoformat)
    parser.add_argument("--date-to", type=date.fromisoformat)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fixture", type=Path, default=REPO_ROOT / "data/contracts/demo_state.json")
    parser.add_argument("--examples", type=Path, default=REPO_ROOT / "data/contracts/examples.json")
    parser.add_argument("--http-examples", type=Path, default=REPO_ROOT / "data/contracts/http-examples.json")
    parser.add_argument("--proposal-example", type=Path, default=REPO_ROOT / "data/contracts/topic-proposal.json")
    parser.add_argument("--workflow-examples", type=Path, default=REPO_ROOT / "data/contracts/workflow-examples.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recordings", type=Path, default=REPO_ROOT / "var/recordings/text")
    parser.add_argument("--authored-input", type=Path)
    parser.add_argument("--requests", type=Path)
    args = parser.parse_args()
    if args.command != "import" and (args.manifest is not None or args.dry_run):
        parser.error("Параметры --manifest и --dry-run доступны только для import.")
    if args.command != "report" and (args.date_from is not None or args.date_to is not None):
        parser.error("Параметры периода доступны только для report.")
    try:
        if args.command == "report":
            from .report import build_report
            settings = Settings()
            path = settings.repo_path(settings.app_db_path)
            if not path.is_file():
                raise RepositoryError("REPORT_DATABASE_MISSING", "База не найдена. Инициализируйте отдельную базу перед отчётом.", 503)
            report = build_report(Repository(path), settings.app_model_mode,
                schemas.RunRequest(date_from=args.date_from, date_to=args.date_to))
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with args.output.open("x", encoding="utf-8") as stream:
                        stream.write(report)
                except FileExistsError:
                    print("REPORT_OUTPUT_EXISTS: Файл уже существует. Выберите новое имя; прежний файл сохранён.", file=sys.stderr)
                    return 1
                print("Markdown-сводка сохранена в UTF-8; исходные обращения не включены.")
            else:
                print(report, end="")
        elif args.command == "import":
            if args.manifest is None:
                parser.error("Для import укажите --manifest с UTF-8 JSON.")
            with args.manifest.open("rb") as stream:
                manifest = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(manifest) > MAX_MANIFEST_BYTES:
                raise RepositoryError("MANIFEST_TOO_LARGE", "Manifest превышает 1 МиБ.", 413)
            settings = Settings()
            repository = Repository(settings.repo_path(settings.app_db_path))
            result = import_tickets(manifest, dry_run=args.dry_run, repository=repository)
            print(result.model_dump_json())
            return 1 if isinstance(result, schemas.ImportPreview) and not result.can_commit else 0
        elif args.command == "verify-recordings":
            queries = None
            if args.requests:
                if args.requests.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError("Request file exceeds limit")
                queries = json.loads(args.requests.read_text(encoding="utf-8"))["entries"]
                if not isinstance(queries, list) or len(queries) > 10000:
                    raise ValueError("Request count exceeds limit")
            report = verify_recordings(args.recordings, queries)
            print(json.dumps(report, ensure_ascii=False))
        elif args.command == "record":
            if args.authored_input is None:
                raise GatewayUnavailable("LIVE_RECORDING_NOT_CONNECTED", "Для live record нужен реальный TextGateway; сейчас доступен только явный authored-вход.")
            target = args.recordings.resolve()
            if not any(target.is_relative_to((REPO_ROOT / folder).resolve()) for folder in ("var", "data/recordings")):
                raise ValueError("Record output must be in var or data/recordings")
            if args.authored_input.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Authored input exceeds 10 MiB")
            bundle = json.loads(args.authored_input.read_text(encoding="utf-8"))
            if bundle.get("synthetic") is not True or not 0 < len(bundle["entries"]) <= 100:
                raise ValueError("Authored input must be a bounded synthetic bundle")
            records = [make_record(TextRequest.model_validate(e["request"]), SCHEMAS[e["schema_name"]], e["response"],
                model_id=e["model_id"], normalization_hash=e["normalization_hash"], set_version=bundle["version"], origin="authored") for e in bundle["entries"]]
            print(json.dumps(publish_records(target, records), ensure_ascii=False))
        elif args.command == "validate-examples":
            count = validate_examples(args.examples)
            http_count = validate_http_examples(args.http_examples)
            proposal = json.loads(args.proposal_example.read_text(encoding="utf-8"))
            if proposal.get("synthetic") is not True or proposal.get("purpose") != "contract_example_only" or proposal.get("schema") != "ProposedTopic":
                raise ValueError("Нужен явно синтетический пример ProposedTopic")
            schemas.ProposedTopic.model_validate(proposal["value"])
            workflow = json.loads(args.workflow_examples.read_text(encoding="utf-8"))
            if workflow.get("synthetic") is not True or workflow.get("purpose") != "contract_examples_only":
                raise ValueError("Нужны синтетические примеры ручных операций")
            workflow_schemas = {**WORKFLOW_SCHEMAS, "DecisionUpdate": schemas.DecisionUpdate}
            for example in workflow["examples"]:
                workflow_schemas[example["schema"]].model_validate(example["value"])
            fixture = load_fixture(args.fixture)
            print(f"Примеры: {count}; HTTP-примеры: {http_count}; ProposedTopic: valid; workflow: {len(workflow['examples'])}; источников тестовой карточки: {len(fixture.sources)}; synthetic=true, origin=authored. База не изменена.")
        elif args.command == "export-schemas":
            output = args.output or REPO_ROOT / "var/contracts/schemas.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({name: cls.model_json_schema() for name, cls in (EXAMPLE_SCHEMAS | STORAGE_SCHEMAS | PIPELINE_SCHEMAS | WORKFLOW_SCHEMAS).items()}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("JSON Schema контрактов сохранены.")
        else:
            settings = Settings()
            path = settings.repo_path(settings.app_db_path)
            initialized = initialize(path)
            if args.command == "init-db":
                print(f"SQLite v{initialized.version} готова. Данные и решения не сбрасывались." + (" Перед миграцией создана резервная копия." if initialized.backup_path else ""))
            else:
                inserted = Repository(path).seed_demo(load_fixture(args.fixture))
                print("Тестовая карточка добавлена; synthetic=true, origin=authored. Анализ моделей не выполнялся." if inserted else "Точное повторение: unchanged. Решения и история сохранены.")
        return 0
    except (ValidationError, ValueError, KeyError, TypeError, AttributeError, OSError, RepositoryError, GatewayUnavailable, sqlite3.Error, RuntimeError) as exc:
        # Не выводим ValidationError целиком: он может включать входные данные.
        code = exc.code if isinstance(exc, (RepositoryError, GatewayUnavailable)) else "COMMAND_FAILED"
        message = str(exc) if isinstance(exc, GatewayUnavailable) else "Команда не завершена. Проверьте файл, контракт и доступ к базе."
        print(f"{code}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
