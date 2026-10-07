"""Deterministic scorers for a non-deterministic system.

No scorer calls a model, so the harness cannot produce a passing grade it has
not verified. The evaluation dimensions covered:

| Brief term        | Scorer here                                         |
| ----------------- | --------------------------------------------------- |
| groundedness      | `score_groundedness` — factors cite resolvable evidence |
| completeness      | `score_completeness` — expected dimensions and evidence kinds present |
| tool correctness  | `score_routing`, `score_deterministic_lookups`      |
| latency / cost    | recorded by the harness from the gateway ledger      |
| regression        | baseline comparison in `harness.py`                  |
| self-consistency  | `score_critic` - the review is present and coherent  |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from sentinel.evaluation.cases import GoldenCase


@dataclass
class CheckResult:
    """One assertion about a result, with the observation that produced it."""

    name: str
    passed: bool
    detail: str
    # False for checks the case did not ask for; excluded from the score.
    applicable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class CaseScore:
    """Every check for one case, plus the derived pass rate."""

    case_id: str
    subject: str
    checks: List[CheckResult] = field(default_factory=list)
    error: str = ""

    @property
    def applicable(self) -> List[CheckResult]:
        return [c for c in self.checks if c.applicable]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.applicable if c.passed)

    @property
    def total(self) -> int:
        return len(self.applicable)

    @property
    def rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0

    @property
    def ok(self) -> bool:
        return not self.error and self.passed == self.total and self.total > 0

    @property
    def failures(self) -> List[CheckResult]:
        return [c for c in self.applicable if not c.passed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "subject": self.subject,
            "passed": self.passed,
            "total": self.total,
            "rate": self.rate,
            "ok": self.ok,
            "error": self.error,
            "failures": [c.to_dict() for c in self.failures],
        }


def _assessment(result: Dict[str, Any]) -> Dict[str, Any]:
    return result.get("risk_assessment") or {}


def _evidence(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _assessment(result).get("evidence") or []


# --- structural integrity ----------------------------------------------------

def score_schema(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """The response carries the shape the API promises."""
    assessment = _assessment(result)

    if case.expect_empty:
        return [CheckResult(
            "schema.survives_empty_request",
            isinstance(result.get("id"), str) and bool(result.get("id")),
            "A degenerate request must still produce a persisted record.",
        )]

    dimensions = assessment.get("dimensions") or []
    return [
        CheckResult(
            "schema.assessment_present",
            bool(assessment),
            "risk_assessment present" if assessment else "no risk_assessment returned",
        ),
        CheckResult(
            "schema.five_dimensions",
            len(dimensions) == 5,
            f"{len(dimensions)} dimensions (expected 5)",
        ),
        CheckResult(
            "schema.score_in_range",
            isinstance(assessment.get("score"), (int, float)) and 0 <= assessment["score"] <= 100,
            f"score={assessment.get('score')}",
        ),
        CheckResult(
            "schema.band_matches_score",
            _band_for(assessment.get("score", -1)) == assessment.get("band"),
            f"score {assessment.get('score')} -> band {assessment.get('band')}",
        ),
        CheckResult(
            "schema.weights_sum_to_one",
            abs(sum(d.get("weight", 0) for d in dimensions) - 1.0) < 0.001,
            f"weights sum to {sum(d.get('weight', 0) for d in dimensions):.3f}",
        ),
    ]


def _band_for(score: float) -> str:
    if score >= 80:
        return "SEVERE"
    if score >= 60:
        return "ELEVATED"
    if score >= 40:
        return "MODERATE"
    if score >= 20:
        return "LOW"
    return "MINIMAL"


# --- groundedness ------------------------------------------------------------

def score_groundedness(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """Every scored factor rests on evidence, and every citation resolves.

    This is the check that would catch a regression to model-declared citations:
    an id that no longer exists in the pool means the chain is broken.
    """
    if case.expect_empty:
        return [CheckResult("groundedness.skipped", True, "no evidence expected", applicable=False)]

    assessment = _assessment(result)
    dimensions = assessment.get("dimensions") or []
    pool_ids = {item.get("id") for item in _evidence(result)}

    assessed = [d for d in dimensions if d.get("assessed")]
    grounded = [d for d in assessed if d.get("grounded")]

    dangling = [
        eid
        for d in dimensions
        for eid in (d.get("evidence_ids") or [])
        if eid not in pool_ids
    ]

    return [
        CheckResult(
            "groundedness.all_assessed_factors_grounded",
            len(grounded) == len(assessed) and bool(assessed),
            f"{len(grounded)}/{len(assessed)} assessed factors cite evidence",
        ),
        CheckResult(
            "groundedness.citations_resolve",
            not dangling,
            "every cited id resolves" if not dangling else f"{len(dangling)} dangling: {dangling[:3]}",
        ),
        CheckResult(
            "groundedness.every_record_has_a_source",
            all(str(item.get("source", "")).strip() for item in _evidence(result)),
            f"{len(_evidence(result))} evidence records checked",
        ),
        CheckResult(
            "groundedness.confidence_by_provenance",
            _confidence_is_sane(_evidence(result)),
            "filings rank above open-source reporting",
        ),
    ]


def _confidence_is_sane(evidence: Sequence[Dict[str, Any]]) -> bool:
    """A press article must never be filed at the confidence of a regulatory filing.

    Without this, the scorer would weigh rumour and disclosure equally — the
    single most damaging thing that could quietly go wrong in the evidence layer.
    """
    for item in evidence:
        if item.get("kind") == "open_source" and item.get("confidence") == "high":
            return False
        if item.get("kind") == "filing" and item.get("confidence") == "low":
            return False
    return True


# --- completeness ------------------------------------------------------------

def score_completeness(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """The run gathered what this case says it should have."""
    if case.expect_empty:
        return [CheckResult("completeness.skipped", True, "nothing expected", applicable=False)]

    evidence = _evidence(result)
    kinds = {item.get("kind") for item in evidence}
    missing = [k for k in case.expected_evidence_kinds if k not in kinds]

    checks = [
        CheckResult(
            "completeness.minimum_evidence",
            len(evidence) >= case.min_evidence,
            f"{len(evidence)} records (expected >= {case.min_evidence})",
        )
    ]
    if case.expected_evidence_kinds:
        checks.append(CheckResult(
            "completeness.expected_evidence_kinds",
            not missing,
            f"present: {sorted(k for k in kinds if k)}" if not missing else f"missing: {missing}",
        ))
    return checks


# --- calibration -------------------------------------------------------------

def score_calibration(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """The composite lands in the range this case expects.

    A range rather than a value: the judgement dimensions come from a model and
    will move between runs. What must not move is the band the entity belongs in.
    """
    if case.expect_empty:
        return [CheckResult("calibration.skipped", True, "no verdict expected", applicable=False)]

    score = _assessment(result).get("score")
    low, high = case.score_range
    inside = isinstance(score, (int, float)) and low <= score <= high

    return [CheckResult(
        "calibration.score_in_expected_range",
        inside,
        f"score={score} expected {low}-{high} ({_band_for(score if isinstance(score, (int, float)) else -1)})",
    )]


# --- tool correctness --------------------------------------------------------

def score_deterministic_lookups(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """Screening and filing lookups are data, not judgement — so they are exact."""
    checks: List[CheckResult] = []

    if case.expected_sanctions is not None:
        actual = (result.get("sanctions_data") or {}).get("status")
        checks.append(CheckResult(
            "tools.sanctions_status",
            actual == case.expected_sanctions,
            f"got {actual}, expected {case.expected_sanctions}",
        ))

    if case.expected_edgar_match is not None:
        actual = bool((result.get("edgar_data") or {}).get("matched"))
        checks.append(CheckResult(
            "tools.edgar_match",
            actual == case.expected_edgar_match,
            f"matched={actual}, expected {case.expected_edgar_match}",
        ))

    return checks


def score_routing(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """The supervisor engaged the specialists this case expects, and explained the rest."""
    plan = result.get("plan") or {}
    engaged = tuple(plan.get("engaged") or ())
    checks: List[CheckResult] = []

    if case.expected_agents is not None:
        checks.append(CheckResult(
            "routing.engaged_specialists",
            set(engaged) == set(case.expected_agents),
            f"engaged {sorted(engaged)}, expected {sorted(case.expected_agents)}",
        ))

    if case.expected_strategy:
        checks.append(CheckResult(
            "routing.strategy",
            plan.get("strategy") == case.expected_strategy,
            f"got {plan.get('strategy')}, expected {case.expected_strategy}",
        ))

    # M-02: a routing decision that is not explained is not logged.
    decisions = plan.get("decisions") or []
    checks.append(CheckResult(
        "routing.every_decision_has_a_reason",
        bool(decisions) and all(str(d.get("reason", "")).strip() for d in decisions),
        f"{len(decisions)} decisions, all with reasons" if decisions else "no decisions recorded",
    ))

    return checks


def score_critic(result: Dict[str, Any], case: GoldenCase) -> List[CheckResult]:
    """The critic ran, and what it produced is well formed.

    Structural rather than behavioural on purpose. Asserting *which* objections
    the critic raises would bind the suite to one model's phrasing on one day -
    the §10.3 mistake. What must hold every time is that a review exists, that
    its disposition follows from its own challenges, and that every objection is
    actionable.
    """
    if case.expect_empty:
        return [CheckResult("critic.skipped", True, "no verdict to review", applicable=False)]

    review = result.get("critic_review") or {}
    if not review:
        return [CheckResult("critic.review_present", False, "no critic review was recorded")]

    challenges = review.get("challenges") or []
    blocking = [c for c in challenges if c.get("severity") == "blocking"]
    material = [c for c in challenges if c.get("severity") == "material"]

    if blocking:
        expected = "rejected"
    elif material:
        expected = "qualified"
    else:
        expected = "endorsed"

    return [
        CheckResult(
            "critic.review_present",
            bool(review.get("verdict")),
            f"verdict={review.get('verdict')}, {len(challenges)} challenge(s)",
        ),
        CheckResult(
            "critic.verdict_follows_challenges",
            review.get("verdict") == expected,
            f"got {review.get('verdict')}, expected {expected} "
            f"({len(blocking)} blocking, {len(material)} material)",
        ),
        CheckResult(
            "critic.every_challenge_is_actionable",
            all(str(c.get("detail", "")).strip() for c in challenges),
            f"{len(challenges)} challenge(s) checked for a stated detail",
        ),
        CheckResult(
            # The critic may lower confidence and never raise it.
            "critic.penalty_is_a_deduction",
            float(review.get("confidence_penalty") or 0.0) >= 0.0,
            f"penalty={review.get('confidence_penalty')}",
        ),
    ]


SCORERS = (
    score_schema,
    score_groundedness,
    score_completeness,
    score_calibration,
    score_deterministic_lookups,
    score_routing,
    score_critic,
)


def score_case(result: Dict[str, Any], case: GoldenCase) -> CaseScore:
    """Runs every scorer against one completed investigation."""
    outcome = CaseScore(case_id=case.id, subject=case.label)
    for scorer in SCORERS:
        try:
            outcome.checks.extend(scorer(result, case))
        except Exception as exc:  # a broken scorer must not mask the result
            outcome.checks.append(CheckResult(
                f"{scorer.__name__}.crashed", False, f"{type(exc).__name__}: {exc}"
            ))
    return outcome
