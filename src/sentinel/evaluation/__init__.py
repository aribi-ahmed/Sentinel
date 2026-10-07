"""Evaluation — CI for agent behaviour.

Unit tests confirm the code does what it says. This package confirms the
*system* still reaches the right conclusions after a prompt is reworded, a
weight retuned or a model swapped — none of which a unit test can observe.

    python -m sentinel.evaluation                  # run and compare to baseline
    python -m sentinel.evaluation --save-baseline  # record a new baseline
    python -m sentinel.evaluation --case alphabet-low
"""

from sentinel.evaluation.cases import GOLDEN_CASES, GoldenCase, case_by_id
from sentinel.evaluation.harness import (
    EvaluationReport,
    compare_to_baseline,
    evaluate,
    load_baseline,
    render_comparison,
    save_baseline,
)
from sentinel.evaluation.scorers import CaseScore, CheckResult, score_case

__all__ = [
    "CaseScore",
    "CheckResult",
    "EvaluationReport",
    "GOLDEN_CASES",
    "GoldenCase",
    "case_by_id",
    "compare_to_baseline",
    "evaluate",
    "load_baseline",
    "render_comparison",
    "save_baseline",
    "score_case",
]
