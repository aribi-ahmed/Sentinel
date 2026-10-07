"""Runs the golden dataset and reports regressions.

Unit tests verify the code; this verifies that the system still reaches the
right conclusions after a prompt is reworded, a weight retuned or a model
swapped. Produces a per-run report (pass rate, latency, cost from the gateway
ledger) and a comparison against a stored baseline.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from sentinel.evaluation.cases import GOLDEN_CASES, GoldenCase
from sentinel.evaluation.scorers import CaseScore, score_case

BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
DEFAULT_BASELINE = BASELINE_DIR / "latest.json"

# A drop larger than this against the baseline is treated as a regression rather
# than model variance.
REGRESSION_TOLERANCE = 0.05

# `None` is a meaningful value for a baseline — it means "there is none" — so it
# cannot double as "argument omitted, read the file from disk".
_LOAD_FROM_DISK = object()


@dataclass
class EvaluationReport:
    """The result of one pass over the dataset."""

    scores: List[CaseScore] = field(default_factory=list)
    started_at: str = ""
    duration_s: float = 0.0
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def cases(self) -> int:
        return len(self.scores)

    @property
    def passed_cases(self) -> int:
        return sum(1 for s in self.scores if s.ok)

    @property
    def checks_passed(self) -> int:
        return sum(s.passed for s in self.scores)

    @property
    def checks_total(self) -> int:
        return sum(s.total for s in self.scores)

    @property
    def rate(self) -> float:
        return round(self.checks_passed / self.checks_total, 3) if self.checks_total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 1),
            "cases": self.cases,
            "passed_cases": self.passed_cases,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
            "rate": self.rate,
            "usage": self.usage,
            "by_case": {s.case_id: s.to_dict() for s in self.scores},
        }

    def render(self) -> str:
        """A terminal report a human can act on."""
        lines = [
            "",
            "=" * 76,
            f"  SENTINEL evaluation — {self.cases} cases, {self.checks_total} checks",
            "=" * 76,
            "",
        ]
        for score in self.scores:
            mark = "PASS" if score.ok else "FAIL"
            lines.append(f"  [{mark}] {score.case_id:<34} {score.passed:>2}/{score.total:<2}  {score.subject}")
            if score.error:
                lines.append(f"          error: {score.error}")
            for failure in score.failures:
                lines.append(f"          - {failure.name}: {failure.detail}")

        usage = self.usage or {}
        lines += [
            "",
            "-" * 76,
            f"  cases passed : {self.passed_cases}/{self.cases}",
            f"  checks passed: {self.checks_passed}/{self.checks_total}  (rate {self.rate})",
            f"  duration     : {self.duration_s:.1f}s",
        ]
        if usage:
            lines.append(
                f"  model usage  : {usage.get('calls', 0)} calls, "
                f"{usage.get('total_tokens', 0):,} tokens, "
                f"${usage.get('cost_usd', 0):.4f}, "
                f"cache hit rate {usage.get('cache_hit_rate', 0)}"
            )
        lines += ["-" * 76, ""]
        return "\n".join(lines)


def _run_case_via_graph(case: GoldenCase) -> Dict[str, Any]:
    """Executes one case against the real graph, up to the human gate."""
    from sentinel.graph.workflow import app as graph_app

    thread = f"eval-{case.id}-{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread}}

    try:
        graph_app.invoke(
            {
                "investigation_id": thread,
                "subject_name": case.subject_name,
                "ticker": case.ticker,
            },
            config,
        )
    except Exception:
        # The graph stops at the interrupt; that is the expected exit.
        pass

    state = graph_app.get_state(config)
    values = dict(state.values) if state else {}
    # Shaped like the API response so the scorers work on either source.
    values.setdefault("id", thread)
    return values


def evaluate(
    cases: Sequence[GoldenCase] = GOLDEN_CASES,
    *,
    runner: Optional[Callable[[GoldenCase], Dict[str, Any]]] = None,
    verbose: bool = True,
) -> EvaluationReport:
    """Runs each case, scores it, and returns the report."""
    runner = runner or _run_case_via_graph
    report = EvaluationReport(started_at=datetime.now(timezone.utc).isoformat())
    started = time.perf_counter()

    for index, case in enumerate(cases, start=1):
        if verbose:
            print(f"  [{index}/{len(cases)}] {case.id} — {case.label or '(empty request)'}", flush=True)
        try:
            result = runner(case)
            report.scores.append(score_case(result, case))
        except Exception as exc:
            outcome = CaseScore(case_id=case.id, subject=case.label)
            outcome.error = f"{type(exc).__name__}: {exc}"
            report.scores.append(outcome)

    report.duration_s = time.perf_counter() - started

    try:
        from sentinel.llm import get_gateway

        report.usage = get_gateway().ledger.snapshot()
    except Exception:
        report.usage = {}

    return report


# --- baselines ---------------------------------------------------------------

def save_baseline(report: EvaluationReport, path: Path = DEFAULT_BASELINE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return path


def load_baseline(path: Path = DEFAULT_BASELINE) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def compare_to_baseline(
    report: EvaluationReport,
    baseline: Any = _LOAD_FROM_DISK,
    *,
    tolerance: float = REGRESSION_TOLERANCE,
) -> Dict[str, Any]:
    """Names the cases that got worse since the baseline.

    Only degradations are reported as regressions. An improvement is worth
    seeing but must never fail a run, or nobody will ever raise the bar.
    """
    baseline = load_baseline() if baseline is _LOAD_FROM_DISK else baseline
    if not baseline:
        return {"has_baseline": False, "regressions": [], "improvements": [], "delta": 0.0}

    previous = baseline.get("by_case", {})
    regressions, improvements = [], []

    for score in report.scores:
        before = previous.get(score.case_id)
        if not before:
            continue
        drop = round(before.get("rate", 0.0) - score.rate, 3)
        if drop > tolerance:
            regressions.append({
                "case_id": score.case_id,
                "was": before.get("rate"),
                "now": score.rate,
                "failures": [f.name for f in score.failures],
            })
        elif drop < -tolerance:
            improvements.append({"case_id": score.case_id, "was": before.get("rate"), "now": score.rate})

    return {
        "has_baseline": True,
        "baseline_at": baseline.get("started_at"),
        "delta": round(report.rate - baseline.get("rate", 0.0), 3),
        "regressions": regressions,
        "improvements": improvements,
    }


def render_comparison(comparison: Dict[str, Any]) -> str:
    if not comparison.get("has_baseline"):
        return "  No baseline recorded yet — run with --save-baseline to create one.\n"

    lines = [f"  Baseline from {comparison['baseline_at']}  ·  overall delta {comparison['delta']:+.3f}"]
    for item in comparison["improvements"]:
        lines.append(f"    improved   {item['case_id']}: {item['was']} -> {item['now']}")
    for item in comparison["regressions"]:
        lines.append(f"    REGRESSED  {item['case_id']}: {item['was']} -> {item['now']}  {item['failures']}")
    if not comparison["regressions"]:
        lines.append("    no regressions")
    return "\n".join(lines) + "\n"
