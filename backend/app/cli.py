import argparse
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from . import schemas
from .config import REPO_ROOT, Settings
from .db import initialize
from .demo import load_fixture
from .repositories import Repository, RepositoryError

EXAMPLE_SCHEMAS = {
    cls.__name__: cls for cls in (
        schemas.TicketInput, schemas.MaterialInput, schemas.NormalizedDocument,
        schemas.Fragment, schemas.ExtractedNeed, schemas.TopicPassport,
        schemas.TopicAssignment, schemas.BatchAssignment, schemas.ImportPreview,
        schemas.ImportResult, schemas.ReviewItem, schemas.DecisionEvent,
        schemas.DecisionUpdate, schemas.Run, schemas.Topic, schemas.RecordingMeta,
    )
}


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


def main() -> int:
    parser = argparse.ArgumentParser(description="GPN Insight — команды этапа 1")
    parser.add_argument("command", choices=["init-db", "seed-demo", "validate-examples", "export-schemas"])
    parser.add_argument("--fixture", type=Path, default=REPO_ROOT / "data/contracts/demo_state.json")
    parser.add_argument("--examples", type=Path, default=REPO_ROOT / "data/contracts/examples.json")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "var/contracts/schemas.json")
    args = parser.parse_args()
    try:
        if args.command == "validate-examples":
            count = validate_examples(args.examples)
            fixture = load_fixture(args.fixture)
            print(f"Примеры: {count}; источников тестовой карточки: {len(fixture.sources)}; synthetic=true, origin=authored. База не изменена.")
        elif args.command == "export-schemas":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({name: cls.model_json_schema() for name, cls in EXAMPLE_SCHEMAS.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("JSON Schema контрактов сохранены.")
        else:
            settings = Settings()
            path = settings.repo_path(settings.app_db_path)
            initialize(path)
            if args.command == "init-db":
                print("SQLite готова. Данные и решения не сбрасывались.")
            else:
                inserted = Repository(path).seed_demo(load_fixture(args.fixture))
                print("Тестовая карточка добавлена; synthetic=true, origin=authored. Анализ моделей не выполнялся." if inserted else "Точное повторение: unchanged. Решения и история сохранены.")
        return 0
    except (ValidationError, ValueError, OSError, RepositoryError, sqlite3.Error, RuntimeError) as exc:
        # Не выводим ValidationError целиком: он может включать входные данные.
        code = exc.code if isinstance(exc, RepositoryError) else "COMMAND_FAILED"
        print(f"{code}: команда не завершена. Проверьте файл, контракт и доступ к базе.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
