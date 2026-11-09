"""Проверяет опубликованные shared partial/media примеры без БД и моделей."""

import json
import sys

from app.cli import PROCESSING_SCHEMAS
from app.config import REPO_ROOT


def main() -> int:
    try:
        value = json.loads((REPO_ROOT / "data/contracts/partial-processing.json").read_text(encoding="utf-8"))
        if (value.get("synthetic") is not True or value.get("origin") != "authored"
                or value.get("purpose") != "partial_processing_contract_examples_only"
                or value.get("production_pipeline_connected") is not False):
            raise ValueError
        for example in value["examples"]:
            PROCESSING_SCHEMAS[example["schema"]].model_validate(example["value"])
        if len(value["examples"]) != 8:
            raise ValueError
    except Exception:
        print("PROCESSING_CONTRACT_INVALID: Проверьте синтетические shared примеры; входные данные не выводятся.", file=sys.stderr)
        return 1
    print("PASS: 8 partial/media examples; 11 new exported schemas; synthetic/authored; no database or model calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
