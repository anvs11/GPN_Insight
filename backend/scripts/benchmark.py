from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


DECISIONS = {"match", "review", "new"}
SPLITS = {"development", "holdout"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: некорректный JSON: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: ожидался JSON-объект")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def validate_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("Датасет пуст")

    case_ids: set[str] = set()
    group_splits: dict[str, str] = {}

    for index, row in enumerate(rows, start=1):
        prefix = f"Запись {index}"

        for field in ("case_id", "group_id", "split", "input", "expected"):
            if field not in row:
                raise ValueError(f"{prefix}: отсутствует поле {field}")

        case_id = row["case_id"]
        group_id = row["group_id"]
        split = row["split"]

        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{prefix}: case_id должен быть непустой строкой")
        if case_id in case_ids:
            raise ValueError(f"Повторяется case_id: {case_id}")
        case_ids.add(case_id)

        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError(f"{prefix}: group_id должен быть непустой строкой")
        if split not in SPLITS:
            raise ValueError(f"{prefix}: split должен быть development или holdout")

        previous_split = group_splits.setdefault(group_id, split)
        if previous_split != split:
            raise ValueError(
                f"Утечка между выборками: group_id={group_id} "
                f"встречается в {previous_split} и {split}"
            )

        input_data = row["input"]
        expected = row["expected"]

        if not isinstance(input_data, dict) or not isinstance(expected, dict):
            raise ValueError(f"{prefix}: input и expected должны быть объектами")

        need = input_data.get("need")
        topics = input_data.get("topics")

        if not isinstance(need, dict):
            raise ValueError(f"{prefix}: input.need должен быть объектом")
        for field in ("source_id", "fragment_id", "evidence_quote"):
            if not isinstance(need.get(field), str) or not need[field].strip():
                raise ValueError(f"{prefix}: need.{field} должен быть непустой строкой")

        if not isinstance(topics, list):
            raise ValueError(f"{prefix}: input.topics должен быть массивом")

        topic_ids = set()
        for topic in topics:
            if not isinstance(topic, dict) or not isinstance(topic.get("topic_id"), str):
                raise ValueError(f"{prefix}: у каждого паспорта должен быть topic_id")
            topic_ids.add(topic["topic_id"])

        decision = expected.get("decision")
        if decision not in DECISIONS:
            raise ValueError(f"{prefix}: неизвестное expected.decision: {decision}")

        expected_topic_id = expected.get("matched_topic_id")
        if decision == "match":
            if expected_topic_id not in topic_ids:
                raise ValueError(
                    f"{prefix}: matched_topic_id отсутствует во входных паспортах"
                )
        elif expected_topic_id is not None:
            raise ValueError(
                f"{prefix}: matched_topic_id должен быть null для {decision}"
            )

        if decision == "new" and topics:
            # Допускается новая тема при непустом каталоге,
            # но эталон обязан явно подтверждать отсутствие подходящей темы.
            if expected.get("no_matching_topic_confirmed") is not True:
                raise ValueError(
                    f"{prefix}: для new требуется "
                    "no_matching_topic_confirmed=true"
                )


def command_validate(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.input)
    validate_rows(rows)
    counts = Counter(row["split"] for row in rows)
    print(json.dumps({
        "valid": True,
        "cases": len(rows),
        "groups": len({row["group_id"] for row in rows}),
        "development": counts["development"],
        "holdout": counts["holdout"],
    }, ensure_ascii=False, indent=2))


def command_split(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.input)

    if not rows:
        raise ValueError("Исходный датасет пуст")
    if not 0.0 < args.holdout_fraction < 1.0:
        raise ValueError("holdout-fraction должен быть между 0 и 1")

    group_split: dict[str, str] = {}
    threshold = int(args.holdout_fraction * (2**64))

    for row in rows:
        group_id = row.get("group_id")
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError("У каждой записи должен быть непустой group_id")

        digest = hashlib.sha256(
            f"{args.seed}:{group_id}".encode("utf-8")
        ).digest()
        bucket = int.from_bytes(digest[:8], "big")
        group_split[group_id] = (
            "holdout" if bucket < threshold else "development"
        )

    # Защита от случайного пустого split на небольших датасетах.
    if len(group_split) < 2:
        raise ValueError("Для разделения нужны минимум две независимые группы")
    if len(set(group_split.values())) < 2:
        raise ValueError(
            "Хеш-разделение оставило одну выборку пустой. "
            "Добавьте группы или измените seed."
        )

    result = []
    for row in rows:
        item = dict(row)
        item["split"] = group_split[item["group_id"]]
        result.append(item)

    validate_rows(result)
    write_jsonl(args.output, result)

    counts = Counter(row["split"] for row in result)
    print(json.dumps({
        "output": str(args.output),
        "cases": len(result),
        "groups": len(group_split),
        "development": counts["development"],
        "holdout": counts["holdout"],
        "seed": args.seed,
    }, ensure_ascii=False, indent=2))


def command_evaluate(args: argparse.Namespace) -> None:
    gold_rows = read_jsonl(args.gold)
    validate_rows(gold_rows)

    selected = [
        row for row in gold_rows
        if row["split"] == args.split
    ]
    if not selected:
        raise ValueError(f"В выборке {args.split} нет примеров")

    predictions = read_jsonl(args.predictions)
    prediction_by_id: dict[str, dict[str, Any]] = {}

    for prediction in predictions:
        case_id = prediction.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("У предсказания отсутствует case_id")
        if case_id in prediction_by_id:
            raise ValueError(f"Дублируется предсказание для {case_id}")
        if prediction.get("decision") not in DECISIONS:
            raise ValueError(f"Некорректное decision для {case_id}")
        prediction_by_id[case_id] = prediction

    expected_ids = {row["case_id"] for row in selected}
    actual_ids = set(prediction_by_id)

    missing = expected_ids - actual_ids
    extra = actual_ids - expected_ids
    if missing or extra:
        raise ValueError(
            f"Набор предсказаний не совпадает с эталоном. "
            f"Пропущено: {sorted(missing)}; лишние: {sorted(extra)}"
        )

    decision_correct = 0
    match_gold = 0
    match_predicted = 0
    match_true_positive = 0
    exact_match_correct = 0
    confusion = {label: Counter() for label in DECISIONS}

    for row in selected:
        case_id = row["case_id"]
        gold = row["expected"]
        pred = prediction_by_id[case_id]

        gold_decision = gold["decision"]
        pred_decision = pred["decision"]

        confusion[gold_decision][pred_decision] += 1
        decision_correct += gold_decision == pred_decision

        is_gold_match = gold_decision == "match"
        is_pred_match = pred_decision == "match"

        match_gold += is_gold_match
        match_predicted += is_pred_match
        match_true_positive += is_gold_match and is_pred_match

        if (
            is_gold_match
            and is_pred_match
            and gold.get("matched_topic_id") == pred.get("matched_topic_id")
        ):
            exact_match_correct += 1

    total = len(selected)
    precision = (
        match_true_positive / match_predicted if match_predicted else 0.0
    )
    recall = match_true_positive / match_gold if match_gold else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )

    result = {
        "split": args.split,
        "cases": total,
        "decision_accuracy": decision_correct / total,
        "match_precision": precision,
        "match_recall": recall,
        "match_f1": f1,
        "exact_topic_accuracy": (
            exact_match_correct / match_gold if match_gold else 0.0
        ),
        "gold_match_count": match_gold,
        "predicted_match_count": match_predicted,
        "exact_topic_correct": exact_match_correct,
        "confusion_matrix": {
            gold_label: {
                pred_label: confusion[gold_label][pred_label]
                for pred_label in sorted(DECISIONS)
            }
            for gold_label in sorted(DECISIONS)
        },
    }

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Независимый benchmark для классификации потребностей"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("input", type=Path)
    validate_parser.set_defaults(func=command_validate)

    split_parser = commands.add_parser("split")
    split_parser.add_argument("input", type=Path)
    split_parser.add_argument("output", type=Path)
    split_parser.add_argument("--seed", default="gpn-insight-benchmark-v1")
    split_parser.add_argument("--holdout-fraction", type=float, default=0.2)
    split_parser.set_defaults(func=command_split)

    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("gold", type=Path)
    evaluate_parser.add_argument("predictions", type=Path)
    evaluate_parser.add_argument(
        "--split", choices=sorted(SPLITS), default="development"
    )
    evaluate_parser.add_argument("--output", type=Path)
    evaluate_parser.set_defaults(func=command_evaluate)

    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, OSError) as exc:
        print(f"Ошибка benchmark: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()