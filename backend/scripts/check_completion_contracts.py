"""Проверка shared примеров A4–A7 без обращения к БД/моделям."""

import json
import sys

from app.assessment_schemas import TopicAssessmentSummary
from app.config import REPO_ROOT
from app.processing_schemas import ProcessingDetail
from app.schemas import Run
from app.weekly import CompletedWeek


def main():
    try:
        values = json.loads((REPO_ROOT / "data/contracts/server-completion.json").read_text(encoding="utf-8"))
        if values["synthetic"] is not True or values["origin"] != "authored" or values["live_model_calls"] is not False:
            raise ValueError
        for name, cls in {"processing": ProcessingDetail, "topic_summary": TopicAssessmentSummary,
                          "completed_week": CompletedWeek, "completed_run": Run}.items():
            cls.model_validate(values[name])
    except Exception:
        print("COMPLETION_CONTRACT_INVALID: Проверьте synthetic примеры; входные данные не выводятся.", file=sys.stderr)
        return 1
    print("PASS: 4 A4-A7 HTTP examples; synthetic/authored; no database or model calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
