"""Проверяет только synthetic формы новых HTTP контрактов, без моделей и БД."""

import json
from app.cli import WORKSPACE_SCHEMAS
from app.config import REPO_ROOT


def main():
    bundle = json.loads((REPO_ROOT / "data/contracts/workspace.json").read_text(encoding="utf-8"))
    assert bundle["synthetic"] is True and bundle["origin"] == "authored" and bundle["purpose"] == "contract_examples_only"
    seen = set()
    for example in bundle["examples"]:
        name = example["schema"]
        assert name not in seen
        WORKSPACE_SCHEMAS[name].model_validate(example["value"])
        seen.add(name)
    assert seen == WORKSPACE_SCHEMAS.keys()
    print(f"PASS: {len(seen)} workspace/archive contracts; synthetic shapes only; no database or model calls")


if __name__ == "__main__":
    main()
