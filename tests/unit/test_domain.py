"""Domain unit tests.

These are the "deterministic seams" §10.3 says to test exhaustively: scoring
arithmetic, band boundaries, the evidence chain and the aggregate's lifecycle.
No model, no database, no network — the whole file runs in milliseconds, which
is what makes it affordable to run on every save.
"""

from __future__ import annotations

import pytest

from sentinel.domain import (
    Confidence,
    Evidence,
    EvidenceKind,
    EvidencePool,
    Finding,
    HumanDecision,
    Investigation,
    RiskAssessment,
    RiskBand,
    RiskFactor,
    Severity,
    Subject,
    Verdict,
    compose,
)


def make_evidence(**overrides) -> Evidence:
    defaults = dict(
        summary="OFAC SDN screening returned CLEAR",
        kind=EvidenceKind.WATCHLIST,
        source="OFAC SDN list (local)",
        collector="compliance_analyst",
        confidence=Confidence.HIGH,
    )
    return Evidence(**{**defaults, **overrides})


# --- Evidence ---------------------------------------------------------------

class TestEvidence:
    def test_ids_are_unique_and_prefixed(self) -> None:
        ids = {make_evidence().id for _ in range(200)}
        assert len(ids) == 200
        assert all(eid.startswith("ev_") for eid in ids)

    def test_a_fact_without_a_source_is_not_evidence(self) -> None:
        with pytest.raises(ValueError, match="source"):
            make_evidence(source="   ")

    def test_a_fact_without_a_summary_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="summary"):
            make_evidence(summary="")

    def test_confidence_carries_a_weight_ordering(self) -> None:
        assert Confidence.HIGH.weight > Confidence.MEDIUM.weight > Confidence.LOW.weight

    def test_round_trips_through_a_dict(self) -> None:
        original = make_evidence(url="https://sec.gov", metadata={"cik": "0001652044"})
        restored = Evidence.from_dict(original.to_dict())

        assert restored.id == original.id
        assert restored.kind is original.kind
        assert restored.confidence is original.confidence
        assert restored.metadata == {"cik": "0001652044"}
        assert restored.collected_at == original.collected_at

    def test_is_immutable(self) -> None:
        with pytest.raises(Exception):
            make_evidence().summary = "tampered"  # type: ignore[misc]


class TestEvidencePool:
    def test_resolves_ids_back_to_records(self) -> None:
        pool = EvidencePool()
        first, second = pool.add(make_evidence()), pool.add(make_evidence())

        assert pool.resolve([first.id, second.id]) == [first, second]

    def test_skips_ids_it_does_not_hold(self) -> None:
        """A dangling citation must not raise — the critic reports it instead."""
        pool = EvidencePool()
        known = pool.add(make_evidence())

        assert pool.resolve([known.id, "ev_deadbeef"]) == [known]

    def test_filters_by_collector_and_kind(self) -> None:
        pool = EvidencePool()
        pool.add(make_evidence(collector="financial_analyst", kind=EvidenceKind.MARKET_DATA))
        pool.add(make_evidence(collector="news_analyst", kind=EvidenceKind.OPEN_SOURCE))

        assert len(pool.by_collector("financial_analyst")) == 1
        assert len(pool.by_kind(EvidenceKind.OPEN_SOURCE)) == 1

    def test_round_trips_through_a_list(self) -> None:
        pool = EvidencePool([make_evidence(), make_evidence()])
        assert len(EvidencePool.from_list(pool.to_list())) == 2


# --- Findings and factors ---------------------------------------------------

class TestFinding:
    def test_a_finding_without_evidence_is_not_grounded(self) -> None:
        assert not Finding(claim="Risky", severity=Severity.HIGH, author="supervisor").is_grounded

    def test_a_finding_with_evidence_is_grounded(self) -> None:
        finding = Finding(
            claim="Designated on the OFAC SDN list",
            severity=Severity.CRITICAL,
            author="compliance_analyst",
            evidence_ids=("ev_1",),
        )
        assert finding.is_grounded

    def test_a_claim_is_mandatory(self) -> None:
        with pytest.raises(ValueError, match="claim"):
            Finding(claim="  ", severity=Severity.LOW, author="x")


class TestRiskFactor:
    def make(self, **overrides) -> RiskFactor:
        defaults = dict(
            id="sanctions", label="Sanctions & watchlists", score=95.0,
            weight=0.30, rationale="Matches a designated entity.",
            evidence_ids=("ev_1",),
        )
        return RiskFactor(**{**defaults, **overrides})

    @pytest.mark.parametrize("score", [-1.0, 100.1, 1000.0])
    def test_rejects_scores_outside_the_scale(self, score: float) -> None:
        with pytest.raises(ValueError, match="outside 0-100"):
            self.make(score=score)

    @pytest.mark.parametrize("weight", [-0.1, 1.5])
    def test_rejects_weights_outside_zero_to_one(self, weight: float) -> None:
        with pytest.raises(ValueError, match="outside 0-1"):
            self.make(weight=weight)

    def test_contribution_is_score_times_weight(self) -> None:
        assert self.make(score=80.0, weight=0.25).contribution == 20.0

    def test_an_assessed_factor_with_no_evidence_is_ungrounded(self) -> None:
        assert not self.make(evidence_ids=(), signals=()).is_grounded

    def test_computed_signals_also_count_as_grounding(self) -> None:
        factor = self.make(evidence_ids=(), signals=({"metric": "P/E", "value": "17.1"},))
        assert factor.is_grounded


# --- Bands ------------------------------------------------------------------

class TestRiskBand:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0, RiskBand.MINIMAL), (19.9, RiskBand.MINIMAL),
            (20, RiskBand.LOW), (39.9, RiskBand.LOW),
            (40, RiskBand.MODERATE), (59.9, RiskBand.MODERATE),
            (60, RiskBand.ELEVATED), (79.9, RiskBand.ELEVATED),
            (80, RiskBand.SEVERE), (100, RiskBand.SEVERE),
        ],
    )
    def test_boundaries_are_inclusive_at_the_floor(self, score: float, expected: RiskBand) -> None:
        assert RiskBand.for_score(score) is expected

    def test_bands_are_ordered(self) -> None:
        assert RiskBand.MINIMAL < RiskBand.LOW < RiskBand.MODERATE < RiskBand.ELEVATED < RiskBand.SEVERE


# --- Composition ------------------------------------------------------------

class TestCompose:
    def factors(self, *scores: float) -> tuple[RiskFactor, ...]:
        weights = (0.30, 0.25, 0.20, 0.15, 0.10)
        return tuple(
            RiskFactor(id=f"f{i}", label=f"F{i}", score=score, weight=weight, rationale="")
            for i, (score, weight) in enumerate(zip(scores, weights))
        )

    def test_weighted_average_with_no_escalation(self) -> None:
        # The Alphabet profile: 5(.30) + 45(.25) + 14(.20) + 55(.15) + 40(.10).
        score, escalated = compose(self.factors(5, 45, 14, 55, 40))

        assert score == pytest.approx(27.8, abs=0.01)
        assert RiskBand.for_score(score) is RiskBand.LOW
        assert not escalated

    def test_a_categorical_finding_lifts_a_diluted_composite(self) -> None:
        """The Wolfspeed case: bankrupt but unsanctioned must not read MODERATE."""
        score, escalated = compose(self.factors(5, 90, 66, 45, 45), escalation_floor=90.0)

        assert escalated
        assert score == pytest.approx(67.5)
        assert RiskBand.for_score(score) is RiskBand.ELEVATED

    def test_escalation_never_lowers_a_score(self) -> None:
        plain, _ = compose(self.factors(95, 85, 60, 70, 65))
        lifted, escalated = compose(self.factors(95, 85, 60, 70, 65), escalation_floor=95.0)

        assert lifted >= plain
        assert not escalated  # the weighted average already exceeded the lift

    def test_findings_below_the_trigger_do_not_escalate(self) -> None:
        _, escalated = compose(self.factors(5, 38, 14, 45, 40), escalation_floor=38.0)
        assert not escalated

    def test_result_is_clamped_to_the_scale(self) -> None:
        score, _ = compose(self.factors(100, 100, 100, 100, 100), escalation_floor=100.0)
        assert 0.0 <= score <= 100.0


# --- Assessment -------------------------------------------------------------

class TestRiskAssessment:
    def build(self, **overrides) -> RiskAssessment:
        defaults = dict(
            score=67.5,
            factors=(
                RiskFactor(id="sanctions", label="Sanctions", score=5, weight=0.30,
                           rationale="Clear", evidence_ids=("ev_1",)),
                RiskFactor(id="legal", label="Legal", score=90, weight=0.25,
                           rationale="Bankruptcy filed", evidence_ids=("ev_2", "ev_3")),
                RiskFactor(id="reputational", label="Reputational", score=75, weight=0.15,
                           rationale="No sources cited"),
            ),
            summary="Bankruptcy proceedings dominate the profile.",
            confidence=1.0,
        )
        return RiskAssessment(**{**defaults, **overrides})

    def test_band_follows_the_score(self) -> None:
        assert self.build().band is RiskBand.ELEVATED

    def test_cited_evidence_is_deduplicated_and_ordered(self) -> None:
        assert self.build().cited_evidence_ids() == ["ev_1", "ev_2", "ev_3"]

    def test_ungrounded_factors_are_reported(self) -> None:
        """This is the hook a critic agent uses to reject an unsupported score."""
        ungrounded = self.build().ungrounded_factors()

        assert [factor.id for factor in ungrounded] == ["reputational"]

    def test_weighted_average_is_kept_alongside_an_escalated_score(self) -> None:
        assessment = self.build(escalated=True)
        assert assessment.weighted_average < assessment.score

    def test_serialisation_resolves_citations_when_given_a_pool(self) -> None:
        pool = EvidencePool()
        cited = pool.add(make_evidence())
        assessment = self.build(
            factors=(RiskFactor(id="sanctions", label="Sanctions", score=95, weight=0.30,
                                rationale="Hit", evidence_ids=(cited.id,)),),
        )

        payload = assessment.to_dict(pool)

        assert payload["cited_evidence"][0]["id"] == cited.id
        assert payload["dimensions"][0]["evidence_ids"] == [cited.id]

    def test_serialisation_without_a_pool_omits_evidence(self) -> None:
        assert "evidence" not in self.build().to_dict()


# --- Investigation lifecycle ------------------------------------------------

class TestInvestigation:
    def make(self) -> Investigation:
        return Investigation(subject=Subject(name="Wolfspeed", ticker="WOLF"))

    def test_a_subject_needs_a_name(self) -> None:
        with pytest.raises(ValueError, match="subject"):
            Subject(name="   ")

    def test_subject_label_handles_a_missing_ticker(self) -> None:
        assert Subject(name="Rosneft").label == "Rosneft"
        assert Subject(name="Wolfspeed", ticker="WOLF").label == "Wolfspeed (WOLF)"

    def test_recording_an_assessment_parks_it_at_the_gate(self) -> None:
        investigation = self.make().with_assessment(
            RiskAssessment(score=67.5, factors=(), summary="s", confidence=1.0)
        )

        assert investigation.awaiting_review
        assert investigation.status.value == "awaiting_review"
        assert not investigation.status.is_terminal

    def test_approval_completes_and_stores_the_report(self) -> None:
        investigation = (
            self.make()
            .with_assessment(RiskAssessment(score=30.0, factors=(), summary="s", confidence=1.0))
            .with_decision(HumanDecision(verdict=Verdict.APPROVED), report_markdown="# Report")
        )

        assert investigation.status.is_terminal
        assert investigation.has_report
        assert not investigation.awaiting_review

    def test_rejection_is_terminal_but_distinct_from_failure(self) -> None:
        investigation = (
            self.make()
            .with_assessment(RiskAssessment(score=30.0, factors=(), summary="s", confidence=1.0))
            .with_decision(HumanDecision(verdict=Verdict.REJECTED))
        )

        assert investigation.status.value == "rejected"
        assert investigation.decision is not None
        assert not investigation.decision.approved

    def test_transitions_do_not_mutate_the_original(self) -> None:
        """The aggregate is immutable, so a checkpointed snapshot stays valid."""
        original = self.make()
        original.with_assessment(RiskAssessment(score=1.0, factors=(), summary="", confidence=1.0))

        assert original.assessment is None
        assert original.status.value == "pending"
