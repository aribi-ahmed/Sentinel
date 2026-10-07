"""Command line entry point for the evaluation harness."""

from __future__ import annotations

import argparse
import sys

from sentinel.evaluation.cases import GOLDEN_CASES, cases_for
from sentinel.evaluation.harness import (
    compare_to_baseline,
    evaluate,
    render_comparison,
    save_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SENTINEL golden dataset.")
    parser.add_argument("--case", action="append", dest="cases",
                        help="run only the named case (repeatable)")
    parser.add_argument("--save-baseline", action="store_true",
                        help="record this run as the baseline for future comparisons")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="exit non-zero if any case scored worse than the baseline")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    selected = cases_for(tuple(args.cases)) if args.cases else GOLDEN_CASES
    if not selected:
        print(f"No cases matched. Known ids: {', '.join(c.id for c in GOLDEN_CASES)}")
        return 2

    print(f"\nRunning {len(selected)} golden case(s). Each is a live investigation.\n")
    report = evaluate(selected, verbose=not args.quiet)
    print(report.render())

    comparison = compare_to_baseline(report)
    print(render_comparison(comparison))

    if args.save_baseline:
        path = save_baseline(report)
        print(f"  Baseline written to {path}\n")

    if args.fail_on_regression and comparison.get("regressions"):
        return 1
    # A case that failed its own expectations is a failure whether or not a
    # baseline exists; otherwise a first run could never fail.
    return 0 if report.passed_cases == report.cases else 1


if __name__ == "__main__":
    sys.exit(main())
