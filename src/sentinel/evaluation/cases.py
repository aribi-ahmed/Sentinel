"""Golden dataset: investigations with known-good outcomes.

Each case states what must be true of a result rather than what it must say. A
composite score is checked against a band range, deterministic lookups
(sanctions status, EDGAR match) are exact, and structural invariants are
asserted directly. Exact model output is never asserted.

Ranges are generous: a case that fails on ordinary model variance trains the
reader to ignore the suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One investigation with the outcome it is expected to produce."""

    id: str
    subject_name: str
    ticker: str
    rationale: str

    # Composite score must land inside this inclusive range.
    score_range: Tuple[float, float] = (0.0, 100.0)
    # Deterministic lookups: exact, because they are not judgement calls.
    expected_sanctions: Optional[str] = None
    expected_edgar_match: Optional[bool] = None
    # The specialists the router should engage. `None` means this case makes no
    # claim about routing; an empty tuple asserts that nothing was engaged, which
    # is a real expectation for a degenerate request.
    expected_agents: Optional[Tuple[str, ...]] = None
    expected_strategy: Optional[str] = None
    # Minimum evidence the run should have gathered.
    min_evidence: int = 0
    expected_evidence_kinds: Tuple[str, ...] = ()
    # A run that is expected to produce nothing at all.
    expect_empty: bool = False

    @property
    def label(self) -> str:
        return f"{self.subject_name} ({self.ticker})" if self.ticker else self.subject_name

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "subject": self.label, "rationale": self.rationale}


ALL_SPECIALISTS = ("research_analyst", "financial_analyst", "news_analyst", "compliance_analyst")
NO_MARKET_DATA = ("research_analyst", "news_analyst", "compliance_analyst")


GOLDEN_CASES: Tuple[GoldenCase, ...] = (
    # --- Clean, well-capitalised issuers -------------------------------------
    GoldenCase(
        id="alphabet-low",
        subject_name="Alphabet Inc.",
        ticker="GOOGL",
        rationale=(
            "The regression anchor for over-flagging. Ordinary antitrust and privacy "
            "exposure at a highly profitable large cap must not read as elevated risk; "
            "before the composite scorer this case returned ELEVATED."
        ),
        score_range=(10.0, 45.0),
        expected_sanctions="CLEAR",
        expected_edgar_match=True,
        expected_agents=ALL_SPECIALISTS,
        expected_strategy="full",
        min_evidence=8,
        expected_evidence_kinds=("filing", "market_data", "watchlist", "policy"),
    ),
    GoldenCase(
        id="microsoft-low",
        subject_name="Microsoft Corporation",
        ticker="MSFT",
        rationale="A second clean large cap: guards against a scorer that only calibrates for one entity.",
        score_range=(5.0, 45.0),
        expected_sanctions="CLEAR",
        expected_edgar_match=True,
        expected_agents=ALL_SPECIALISTS,
        expected_strategy="full",
        min_evidence=8,
    ),

    # --- Financial distress, no sanctions ------------------------------------
    GoldenCase(
        id="wolfspeed-distress",
        subject_name="Wolfspeed",
        ticker="WOLF",
        rationale=(
            "The escalation-rule anchor. Bankruptcy filings must lift the composite even "
            "though the sanctions dimension is clean; the weighted average alone put this "
            "case at 56 MODERATE, which is wrong for a company in Chapter 11."
        ),
        score_range=(55.0, 95.0),
        expected_sanctions="CLEAR",
        expected_edgar_match=True,
        expected_agents=ALL_SPECIALISTS,
        expected_strategy="full",
        min_evidence=8,
        expected_evidence_kinds=("filing", "market_data"),
    ),
    GoldenCase(
        id="lucid-moderate",
        subject_name="Lucid Group",
        ticker="LCID",
        rationale=(
            "Loss-making with live litigation but no findings against it. Holds the middle "
            "of the scale honest: neither clean nor elevated."
        ),
        score_range=(25.0, 65.0),
        expected_sanctions="CLEAR",
        expected_edgar_match=True,
        expected_agents=ALL_SPECIALISTS,
        min_evidence=8,
    ),

    # --- Sanctioned entities --------------------------------------------------
    GoldenCase(
        id="rosneft-sanctioned",
        subject_name="Rosneft",
        ticker="",
        rationale=(
            "A designated entity with no US listing. Exercises both the sanctions path and "
            "the router: with no ticker the financial specialist must be skipped, and the "
            "supervisor must still run."
        ),
        score_range=(60.0, 100.0),
        expected_sanctions="HIT",
        expected_edgar_match=False,
        expected_agents=NO_MARKET_DATA,
        expected_strategy="reduced",
        min_evidence=4,
        expected_evidence_kinds=("watchlist",),
    ),
    GoldenCase(
        id="bank-melli-sanctioned",
        subject_name="Bank Melli Iran",
        ticker="",
        rationale="A second designation, matched through the alias table rather than the primary name.",
        score_range=(60.0, 100.0),
        expected_sanctions="HIT",
        expected_agents=NO_MARKET_DATA,
        expected_strategy="reduced",
        min_evidence=4,
    ),
    GoldenCase(
        id="cuba-bank-sanctioned",
        subject_name="Banco Nacional de Cuba",
        ticker="",
        rationale="An exact primary-name match on a long-standing programme.",
        score_range=(55.0, 100.0),
        expected_sanctions="HIT",
        expected_agents=NO_MARKET_DATA,
        min_evidence=4,
    ),

    # --- Screening precision --------------------------------------------------
    GoldenCase(
        id="alphabet-not-alphabet-international",
        subject_name="Alphabet Inc.",
        ticker="GOOGL",
        rationale=(
            "A false-positive guard. The SDN list contains ALPHABET INTERNATIONAL DMCC; an "
            "early matcher flagged Alphabet Inc. against it. This case fails if that "
            "returns."
        ),
        score_range=(10.0, 45.0),
        expected_sanctions="CLEAR",
        min_evidence=8,
    ),

    # --- Routing edge cases ---------------------------------------------------
    GoldenCase(
        id="unlisted-private-entity",
        subject_name="Acme Trading Limited",
        ticker="",
        rationale=(
            "A private entity with no ticker and no SEC registration. Checks that the system "
            "degrades to a reduced scope and still produces a scored verdict rather than failing."
        ),
        score_range=(0.0, 75.0),
        expected_edgar_match=False,
        expected_agents=NO_MARKET_DATA,
        expected_strategy="reduced",
        min_evidence=1,
    ),
    GoldenCase(
        id="empty-subject",
        subject_name="",
        ticker="",
        rationale=(
            "The degenerate request. Nothing should be engaged, nothing should crash, and "
            "the verdict must not claim confidence it has not earned."
        ),
        expected_agents=(),
        expected_strategy="reduced",
        expect_empty=True,
    ),
)


def case_by_id(case_id: str) -> Optional[GoldenCase]:
    return next((case for case in GOLDEN_CASES if case.id == case_id), None)


def cases_for(tags: Optional[Tuple[str, ...]] = None) -> Tuple[GoldenCase, ...]:
    """All cases, or a named subset — used to run a quick pass in CI."""
    if not tags:
        return GOLDEN_CASES
    return tuple(case for case in GOLDEN_CASES if case.id in tags)
