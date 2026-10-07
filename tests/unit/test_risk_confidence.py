"""Tests for the confidence calculation.

Written after the critic caught the original version returning exactly 1.0 on
two consecutive live runs. It divided assessed dimensions by total dimensions,
so any complete investigation of a listed issuer was reported as 100% certain -
a *coverage* measure wearing the label "confidence".

The property that matters is discrimination: two runs of differing quality must
not produce the same number.
"""

from __future__ import annotations

import pytest

from sentinel.domain import Confidence, Evidence, EvidenceKind, EvidencePool, RiskFactor
from sentinel.services.risk import CONFIDENCE_CEILING, _confidence


def ev(eid: str, confidence: Confidence = Confidence.HIGH) -> Evidence:
    return Evidence(
        summary="Form 10-K filed",
        kind=EvidenceKind.FILING,
        source="SEC EDGAR",
        collector="research_analyst",
        confidence=confidence,
        id=eid,
    )


def factor(fid: str, *, grounded: bool = True, assessed: bool = True) -> RiskFactor:
    return RiskFactor(
        id=fid,
        label=fid,
        score=25.0,
        weight=0.2,
        rationale="rationale",
        evidence_ids=("ev_1",) if grounded else (),
        assessed=assessed,
    )


def pool_with(*items: Evidence) -> EvidencePool:
    pool = EvidencePool()
    for item in items:
        pool.add(item)
    return pool


FULL = [factor(str(i)) for i in range(5)]


class TestConfidence:
    def test_certainty_is_never_reported(self) -> None:
        """The regression this file exists for."""
        best = _confidence(FULL, pool_with(ev("ev_1")), model_ok=True, quality="")

        assert best == CONFIDENCE_CEILING
        assert best < 1.0

    def test_medium_confidence_sourcing_tempers_the_verdict(self) -> None:
        high = _confidence(FULL, pool_with(ev("ev_1")), model_ok=True, quality="")
        medium = _confidence(
            FULL, pool_with(ev("ev_1", Confidence.MEDIUM)), model_ok=True, quality="",
        )

        assert medium < high

    def test_an_ungrounded_factor_lowers_confidence(self) -> None:
        """The defect the old formula could not see at all."""
        grounded = _confidence(FULL, pool_with(ev("ev_1")), model_ok=True, quality="")
        partial = _confidence(
            [factor("0"), factor("1"), factor("2"), factor("3"), factor("4", grounded=False)],
            pool_with(ev("ev_1")),
            model_ok=True,
            quality="",
        )

        assert partial < grounded

    def test_an_unassessed_dimension_lowers_confidence(self) -> None:
        partial = _confidence(
            [factor("0"), factor("1"), factor("2"), factor("3"), factor("4", assessed=False)],
            pool_with(ev("ev_1")),
            model_ok=True,
            quality="",
        )

        assert partial < CONFIDENCE_CEILING

    def test_a_failed_model_call_lowers_confidence_sharply(self) -> None:
        degraded = _confidence(FULL, pool_with(ev("ev_1")), model_ok=False, quality="")
        assert degraded < 0.7

    def test_thin_evidence_quality_is_applied(self) -> None:
        normal = _confidence(FULL, pool_with(ev("ev_1")), model_ok=True, quality="adequate")
        thin = _confidence(FULL, pool_with(ev("ev_1")), model_ok=True, quality="thin")

        assert thin < normal

    def test_nothing_assessed_is_zero_not_a_guess(self) -> None:
        nothing = [factor(str(i), assessed=False) for i in range(5)]
        assert _confidence(nothing, EvidencePool(), model_ok=True, quality="") == 0.0

    def test_no_factors_at_all_is_zero(self) -> None:
        assert _confidence([], EvidencePool(), model_ok=True, quality="") == 0.0

    def test_uncited_evidence_does_not_inflate_provenance(self) -> None:
        """A pool full of filings nobody cited is not support for the verdict."""
        uncited = pool_with(ev("ev_1"), *[ev(f"ev_extra_{i}") for i in range(20)])
        cited_only = pool_with(ev("ev_1"))

        assert _confidence(FULL, uncited, model_ok=True, quality="") == _confidence(
            FULL, cited_only, model_ok=True, quality="",
        )

    @pytest.mark.parametrize("quality", ["", "adequate", "thin", "STRONG"])
    def test_the_result_is_always_a_valid_probability(self, quality: str) -> None:
        value = _confidence(FULL, pool_with(ev("ev_1")), model_ok=True, quality=quality)
        assert 0.0 <= value <= CONFIDENCE_CEILING


class TestFraudCoherence:
    """A verdict that reads LOW while the fraud panel reads ELEVATED is
    incoherent to anyone looking at both."""

    def test_the_floor_matches_one_band_below_the_fraud_band(self) -> None:
        from sentinel.services.risk import _FRAUD_BAND_FLOOR

        assert _FRAUD_BAND_FLOOR["SEVERE"] == 60.0
        assert _FRAUD_BAND_FLOOR["ELEVATED"] == 40.0
        assert _FRAUD_BAND_FLOOR["MODERATE"] == 20.0

    def test_a_clean_fraud_picture_imposes_no_floor(self) -> None:
        from sentinel.services.risk import _FRAUD_BAND_FLOOR

        assert _FRAUD_BAND_FLOOR.get("LOW") is None
        assert _FRAUD_BAND_FLOOR.get("MINIMAL") is None
