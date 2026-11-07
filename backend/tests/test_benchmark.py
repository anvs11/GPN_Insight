import json

import pytest

from scripts.benchmark import (
    command_evaluate,
    command_split,
    read_jsonl,
    validate_rows,
)


def make_row(case_id, group_id, decision="match"):
    topic_id = "TOPIC-1"
    return {
        "case_id": case_id,
        "group_id": group_id,
        "input": {
            "need": {
                "source_id": f"SOURCE-{case_id}",
                "fragment_id": f"FRAGMENT-{case_id}",
                "evidence_quote": "Нужно проверить оборудование.",
            },
            "topics": [
                {
                    "topic_id": topic_id,
                    "title": "Техническое обслуживание",
                }
            ],
        },
        "expected": {
            "decision": decision,
            "matched_topic_id": topic_id if decision == "match" else None,
            **(
                {"no_matching_topic_confirmed": True}
                if decision == "new"
                else {}
            ),
        },
    }


def test_validate_accepts_independent_labeled_rows():
    rows = [
        {**make_row("CASE-1", "GROUP-1"), "split": "development"},
        {**make_row("CASE-2", "GROUP-2", "review"), "split": "holdout"},
    ]

    validate_rows(rows)


def test_validate_rejects_group_leakage():
    rows = [
        {**make_row("CASE-1", "GROUP-1"), "split": "development"},
        {**make_row("CASE-2", "GROUP-1"), "split": "holdout"},
    ]

    with pytest.raises(ValueError, match="Утечка"):
        validate_rows(rows)


def test_validate_rejects_match_to_unknown_topic():
    row = make_row("CASE-1", "GROUP-1")
    row["expected"]["matched_topic_id"] = "UNKNOWN"
    row["split"] = "development"

    with pytest.raises(ValueError, match="отсутствует"):
        validate_rows([row])


def test_split_is_deterministic_and_keeps_groups_together(tmp_path):
    source = tmp_path / "raw.jsonl"
    output_a = tmp_path / "a.jsonl"
    output_b = tmp_path / "b.jsonl"

    rows = [
        make_row(f"CASE-{i}", f"GROUP-{i}")
        for i in range(30)
    ]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    import argparse

    args = argparse.Namespace(
        input=source,
        output=output_a,
        seed="test-seed",
        holdout_fraction=0.2,
    )
    command_split(args)

    args.output = output_b
    command_split(args)

    assert output_a.read_bytes() == output_b.read_bytes()

    result = read_jsonl(output_a)
    validate_rows(result)
    assert {row["split"] for row in result} == {
        "development",
        "holdout",
    }


def test_evaluate_calculates_decision_and_exact_match_metrics(tmp_path):
    gold = tmp_path / "gold.jsonl"
    predictions = tmp_path / "predictions.jsonl"

    row = {
        **make_row("CASE-1", "GROUP-1"),
        "split": "development",
    }
    gold.write_text(json.dumps(row) + "\n", encoding="utf-8")
    predictions.write_text(
        json.dumps({
            "case_id": "CASE-1",
            "decision": "match",
            "matched_topic_id": "TOPIC-1",
        }) + "\n",
        encoding="utf-8",
    )

    args = type("Args", (), {
        "gold": gold,
        "predictions": predictions,
        "split": "development",
        "output": None,
    })()

    command_evaluate(args)