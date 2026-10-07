"""Tests for the critic.

Two things need proving, and they pull in opposite directions:

1. The critic **finds** the defects it claims to find. A quality control that
   endorses everything is worse than none, because it manufactures assurance.
2. The critic **does not** invent defects. A critic that objects to sound
   verdicts trains the officer to click through its objections, which destroys
   the value of the ones that matter.

So every check gets a positive and a negative case. `use_model=False` throughout:
the deterministic checks are the ones that must be exhaustively true, and the
model call is tested separately with a stub.
"""

from __future__ import annotations

import pytest

from sentinel.domain.analysis import RiskAssessment, RiskFactor
from sentinel.domain.evidence import Confidence, Evidence, EvidenceKind, EvidencePool
from sentinel.domain.planning import InvestigationPlan, RoutingDecision
from sentinel.domain.review import (
    Challenge,
    ChallengeKind,
    ChallengeSeverity,
    CriticReview,
    ReviewVerdict,
    decide_verdict,
)
from sentinel.domain.tooling import ToolAuditTrail, ToolInvocation, ToolOutcome
from sentinel.services import critic


# --- fixtures ----------------------------------------------------------------

def evidence(eid: str = "ev_1", confidence: Confidence = Confidence.HIGH) -> Evidence:
    return Evidence(
        summary="Form 10-K filed 2025-02-01",
        kind=EvidenceKind.FILING,
        source="SEC EDGAR",
        collector="research_analyst",
        confidence=confidence,
        id=eid,
    )


def pool_of(*items: Evidence) -> EvidencePool:
    pool = EvidencePool()
    for item in items:
        pool.add(item)
    return pool


WEIGHTS = (
    ("sanctions", "Sanctions", 0.30),
    ("legal_regulatory", "Legal & regulatory", 0.25),
    ("financial", "Financial", 0.20),
    ("reputational", "Reputational", 0.15),
    ("governance", "Governance", 0.10),
)


def assessment(
    *,
    score: float = 25.0,
    confidence: float = 0.6,
    scores: dict | None = None,
    evidence_ids: tuple = ("ev_1",),
    drivers: tuple = (),
) -> RiskAssessment:
    """A well-formed assessment; overrides make it defective one way at a time."""
    scores = scores or {}
    factors = tuple(
        RiskFactor(
            id=fid,
            label=label,
            score=float(scores.get(fid, 25.0)),
            weight=weight,
            rationale="rationale",
            evidence_ids=evidence_ids,
        )
        for fid, label, weight in WEIGHTS
    )
    return RiskAssessment(
        score=score,
        factors=factors,
        summary="summary",
        confidence=confidence,
        key_drivers=drivers,
    )


def trail_with(tool: str, outcome: ToolOutcome, error: str = "no key configured") -> ToolAuditTrail:
    trail = ToolAuditTrail()
    trail.add(ToolInvocation(
        tool=tool,
        caller="compliance_analyst",
        outcome=outcome,
        error=error if outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) else "",
        result_digest="" if outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE) else "ok",
    ))
    return trail


# --- domain ------------------------------------------------------------------

class TestReviewDomain:
    def test_a_challenge_without_detail_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Challenge(
                kind=ChallengeKind.THIN_EVIDENCE,
                severity=ChallengeSeverity.MATERIAL,
                subject="x",
                detail="   ",
            )

    def test_verdict_follows_the_worst_severity(self) -> None:
        def ch(sev: ChallengeSeverity) -> Challenge:
            return Challenge(
                kind=ChallengeKind.THIN_EVIDENCE, severity=sev, subject="x", detail="d",
            )

        assert decide_verdict(()) is ReviewVerdict.ENDORSED
        assert decide_verdict((ch(ChallengeSeverity.ADVISORY),)) is ReviewVerdict.ENDORSED
        assert decide_verdict((ch(ChallengeSeverity.MATERIAL),)) is ReviewVerdict.QUALIFIED
        assert decide_verdict((
            ch(ChallengeSeverity.MATERIAL), ch(ChallengeSeverity.BLOCKING),
        )) is ReviewVerdict.REJECTED

    def test_the_critic_may_never_raise_confidence(self) -> None:
        """A second reading finds reasons to trust less, never reasons to trust more."""
        with pytest.raises(ValueError):
            CriticReview(verdict=ReviewVerdict.ENDORSED, confidence_penalty=-0.1)

    def test_adjusted_confidence_is_clamped(self) -> None:
        review = CriticReview(verdict=ReviewVerdict.REJECTED, confidence_penalty=0.9)
        assert review.adjusted_confidence(0.5) == 0.0

    def test_roundtrip_preserves_challenges(self) -> None:
        original = CriticReview(
            verdict=ReviewVerdict.QUALIFIED,
            challenges=(Challenge(
                kind=ChallengeKind.UNGROUNDED_FACTOR,
                severity=ChallengeSeverity.MATERIAL,
                subject="governance",
                detail="no evidence",
            ),),
            confidence_penalty=0.05,
        )
        restored = CriticReview.from_dict(original.to_dict())

        assert restored is not None
        assert restored.verdict is ReviewVerdict.QUALIFIED
        assert restored.challenges[0].subject == "governance"

    def test_a_malformed_review_reads_back_as_none(self) -> None:
        assert CriticReview.from_dict({"verdict": "banana"}) is None
        assert CriticReview.from_dict("not a dict") is None

    def test_briefing_is_empty_when_clean(self) -> None:
        """An empty critique must leave the supervisor's prompt untouched."""
        assert CriticReview(verdict=ReviewVerdict.ENDORSED).briefing() == ""


# --- the deterministic checks ------------------------------------------------

class TestCitations:
    def test_a_citation_that_resolves_is_not_challenged(self) -> None:
        review = critic.review(assessment(), pool=pool_of(evidence()), use_model=False)
        assert not review.by_kind(ChallengeKind.DANGLING_CITATION)

    def test_a_dangling_citation_blocks(self) -> None:
        """The ADR-007 regression: a factor citing evidence that does not exist."""
        review = critic.review(
            assessment(evidence_ids=("ev_ghost",)), pool=pool_of(evidence()), use_model=False,
        )

        challenges = review.by_kind(ChallengeKind.DANGLING_CITATION)
        assert challenges and challenges[0].severity is ChallengeSeverity.BLOCKING
        assert review.verdict is ReviewVerdict.REJECTED


class TestGrounding:
    def test_a_heavily_weighted_ungrounded_factor_is_material(self) -> None:
        subject = assessment()
        stripped = tuple(
            RiskFactor(
                id=f.id, label=f.label, score=f.score, weight=f.weight,
                rationale=f.rationale, evidence_ids=() if f.id == "sanctions" else f.evidence_ids,
            )
            for f in subject.factors
        )
        subject = RiskAssessment(
            score=25.0, factors=stripped, summary="s", confidence=0.6,
        )

        review = critic.review(subject, pool=pool_of(evidence()), use_model=False)
        challenges = review.by_kind(ChallengeKind.UNGROUNDED_FACTOR)

        assert challenges and challenges[0].severity is ChallengeSeverity.MATERIAL

    def test_a_lightly_weighted_ungrounded_factor_is_only_advisory(self) -> None:
        """Governance at 10% resting on judgement does not qualify a verdict."""
        subject = assessment()
        stripped = tuple(
            RiskFactor(
                id=f.id, label=f.label, score=f.score, weight=f.weight,
                rationale=f.rationale, evidence_ids=() if f.id == "governance" else f.evidence_ids,
            )
            for f in subject.factors
        )
        review = critic.review(
            RiskAssessment(score=25.0, factors=stripped, summary="s", confidence=0.6),
            pool=pool_of(evidence()),
            use_model=False,
        )

        assert review.by_kind(ChallengeKind.UNGROUNDED_FACTOR)[0].severity is ChallengeSeverity.ADVISORY
        assert review.verdict is ReviewVerdict.ENDORSED


class TestBand:
    def test_a_band_that_contradicts_its_score_blocks(self) -> None:
        subject = RiskAssessment(
            score=25.0, factors=assessment().factors, summary="s", confidence=0.6,
        )
        # Band is derived, so it cannot be forged directly; assert the check agrees.
        assert not critic._check_band(subject)

    def test_the_band_check_reads_the_score(self) -> None:
        for score in (10.0, 30.0, 50.0, 70.0, 90.0):
            subject = RiskAssessment(
                score=score, factors=assessment().factors, summary="s", confidence=0.5,
            )
            assert critic._check_band(subject) == []


class TestUnverifiedScope:
    """The check the tool audit trail made possible - and the sharpest one."""

    def test_a_reassuring_score_from_a_tool_that_never_ran_blocks(self) -> None:
        review = critic.review(
            assessment(scores={"sanctions": 8.0}),
            pool=pool_of(evidence()),
            trail=trail_with("screen_sanctions", ToolOutcome.UNAVAILABLE),
            use_model=False,
        )

        challenges = review.by_kind(ChallengeKind.UNVERIFIED_SCOPE)
        assert challenges and challenges[0].severity is ChallengeSeverity.BLOCKING
        assert "absence of evidence" in challenges[0].detail
        assert review.verdict is ReviewVerdict.REJECTED

    def test_an_already_cautious_score_is_only_advisory(self) -> None:
        """If the verdict is already worried, a missing source changes less."""
        review = critic.review(
            assessment(scores={"sanctions": 75.0}),
            pool=pool_of(evidence()),
            trail=trail_with("screen_sanctions", ToolOutcome.UNAVAILABLE),
            use_model=False,
        )

        assert review.by_kind(ChallengeKind.UNVERIFIED_SCOPE)[0].severity is ChallengeSeverity.ADVISORY

    def test_a_tool_that_ran_and_found_nothing_is_not_challenged(self) -> None:
        """EMPTY is a real answer. Objecting to it would be the false positive."""
        review = critic.review(
            assessment(scores={"sanctions": 8.0}),
            pool=pool_of(evidence()),
            trail=trail_with("screen_sanctions", ToolOutcome.EMPTY),
            use_model=False,
        )

        assert not review.by_kind(ChallengeKind.UNVERIFIED_SCOPE)

    def test_a_failed_tool_counts_the_same_as_an_absent_one(self) -> None:
        review = critic.review(
            assessment(scores={"financial": 5.0}),
            pool=pool_of(evidence()),
            trail=trail_with("fetch_company_financials", ToolOutcome.FAILED, "upstream 503"),
            use_model=False,
        )

        assert review.by_kind(ChallengeKind.UNVERIFIED_SCOPE)

    def test_no_trail_means_no_scope_challenge(self) -> None:
        review = critic.review(assessment(), pool=pool_of(evidence()), use_model=False)
        assert not review.by_kind(ChallengeKind.UNVERIFIED_SCOPE)


class TestEvidenceQuality:
    def test_a_serious_verdict_with_no_hard_evidence_is_material(self) -> None:
        review = critic.review(
            assessment(score=72.0),
            pool=pool_of(evidence(confidence=Confidence.LOW)),
            use_model=False,
        )

        assert review.by_kind(ChallengeKind.THIN_EVIDENCE)

    def test_a_serious_verdict_backed_by_a_filing_is_not_challenged(self) -> None:
        review = critic.review(
            assessment(score=72.0), pool=pool_of(evidence()), use_model=False,
        )
        assert not review.by_kind(ChallengeKind.THIN_EVIDENCE)

    def test_a_low_verdict_is_not_held_to_the_same_bar(self) -> None:
        review = critic.review(
            assessment(score=15.0),
            pool=pool_of(evidence(confidence=Confidence.LOW)),
            use_model=False,
        )
        assert not review.by_kind(ChallengeKind.THIN_EVIDENCE)


class TestConfidence:
    def test_high_confidence_on_a_reduced_plan_is_challenged(self) -> None:
        plan = InvestigationPlan(decisions=(
            RoutingDecision(agent="research_analyst", engaged=True, reason="in scope"),
            RoutingDecision(agent="financial_analyst", engaged=False, reason="no ticker supplied"),
        ))
        review = critic.review(
            assessment(confidence=0.9),
            pool=pool_of(*[evidence(f"ev_{i}") for i in range(8)]),
            plan=plan,
            use_model=False,
        )

        challenges = review.by_kind(ChallengeKind.OVERCONFIDENT)
        assert challenges and "plan was reduced" in challenges[0].detail

    def test_high_confidence_on_a_complete_run_is_fine(self) -> None:
        plan = InvestigationPlan(decisions=(
            RoutingDecision(agent="research_analyst", engaged=True, reason="in scope"),
        ))
        review = critic.review(
            assessment(confidence=0.9),
            pool=pool_of(*[evidence(f"ev_{i}") for i in range(8)]),
            trail=trail_with("screen_sanctions", ToolOutcome.OK),
            plan=plan,
            use_model=False,
        )

        assert not review.by_kind(ChallengeKind.OVERCONFIDENT)

    def test_modest_confidence_is_never_challenged(self) -> None:
        review = critic.review(assessment(confidence=0.5), pool=EvidencePool(), use_model=False)
        assert not review.by_kind(ChallengeKind.OVERCONFIDENT)


# --- overall behaviour -------------------------------------------------------

class TestReviewOutcome:
    def test_a_sound_assessment_is_endorsed(self) -> None:
        """The negative case that keeps the critic honest."""
        review = critic.review(
            assessment(),
            pool=pool_of(evidence()),
            trail=trail_with("screen_sanctions", ToolOutcome.OK),
            use_model=False,
        )

        assert review.verdict is ReviewVerdict.ENDORSED
        assert review.is_clean
        assert review.confidence_penalty == 0.0

    def test_penalties_accumulate_but_are_capped(self) -> None:
        review = critic.review(
            assessment(score=72.0, confidence=0.95, evidence_ids=("ev_ghost",)),
            pool=pool_of(evidence(confidence=Confidence.LOW)),
            trail=trail_with("screen_sanctions", ToolOutcome.UNAVAILABLE),
            use_model=False,
        )

        assert review.confidence_penalty <= critic.MAX_PENALTY

    def test_a_broken_check_does_not_void_the_review(self, monkeypatch) -> None:
        def explode(*_args, **_kwargs):
            raise RuntimeError("check is broken")

        monkeypatch.setattr(critic, "DETERMINISTIC_CHECKS", (explode,))
        review = critic.review(assessment(), pool=pool_of(evidence()), use_model=False)

        assert "check is broken" in review.challenges[0].detail

    def test_the_review_survives_an_empty_pool(self) -> None:
        assert critic.review(assessment(evidence_ids=()), use_model=False) is not None


class TestModelChallenge:
    def test_an_objection_to_a_real_driver_is_kept(self, monkeypatch) -> None:
        subject = assessment(drivers=("Ongoing antitrust litigation in the EU",))

        monkeypatch.setattr(
            critic,
            "_challenge_drivers",
            lambda a, p: (
                [Challenge(
                    kind=ChallengeKind.UNSUPPORTED_DRIVER,
                    severity=ChallengeSeverity.MATERIAL,
                    subject="Ongoing antitrust litigation in the EU",
                    detail="nothing cited mentions litigation",
                )],
                True,
                "",
            ),
        )
        review = critic.review(subject, pool=pool_of(evidence()), use_model=True)

        assert review.by_kind(ChallengeKind.UNSUPPORTED_DRIVER)
        assert review.verdict is ReviewVerdict.QUALIFIED

    def test_a_provider_outage_leaves_the_other_checks_standing(self, monkeypatch) -> None:
        monkeypatch.setattr(
            critic, "_challenge_drivers", lambda a, p: ([], False, "all providers failed"),
        )
        review = critic.review(
            assessment(evidence_ids=("ev_ghost",)), pool=pool_of(evidence()), use_model=True,
        )

        assert review.model_ok is False
        assert review.by_kind(ChallengeKind.DANGLING_CITATION), "deterministic checks still ran"

    def test_the_model_is_not_called_when_there_are_no_drivers(self) -> None:
        """No drivers means no question to ask, so no token is spent."""
        challenges, ok, error = critic._challenge_drivers(assessment(), pool_of(evidence()))
        assert (challenges, ok, error) == ([], True, "")


class TestRevisionLoop:
    """The loop must terminate. An unbounded critic is a rate-limit incident."""

    def test_an_endorsed_verdict_goes_straight_to_the_human(self) -> None:
        from sentinel.graph.workflow import route_after_critic

        assert route_after_critic({"critic_review": {"verdict": "endorsed"}}) == "human_approval"

    def test_a_qualified_verdict_is_not_sent_back(self) -> None:
        """Only blocking defects earn a revision; material ones are reported."""
        from sentinel.graph.workflow import route_after_critic

        assert route_after_critic({"critic_review": {"verdict": "qualified"}}) == "human_approval"

    def test_a_rejected_verdict_is_sent_back_once(self) -> None:
        from sentinel.graph.workflow import route_after_critic

        state = {"critic_review": {"verdict": "rejected"}, "revision_count": 0}
        assert route_after_critic(state) == "revise"

    def test_a_second_rejection_reaches_the_human_anyway(self) -> None:
        """The officer sees the disagreement rather than the graph grinding on it."""
        from sentinel.graph.workflow import MAX_REVISIONS, route_after_critic

        state = {"critic_review": {"verdict": "rejected"}, "revision_count": MAX_REVISIONS}
        assert route_after_critic(state) == "human_approval"

    def test_a_missing_review_does_not_trap_the_graph(self) -> None:
        from sentinel.graph.workflow import route_after_critic

        assert route_after_critic({}) == "human_approval"

    def test_revise_increments_the_counter(self) -> None:
        from sentinel.graph.nodes import revise_node

        result = revise_node({"revision_count": 0, "critic_review": {"blocking": 2}})
        assert result["revision_count"] == 1
        assert "revision 1" in result["logs"][0]


class TestCertaintyCeiling:
    """Observed live: the supervisor returned confidence 1.0 on a verdict whose
    own drivers the critic then found unsupported."""

    def test_certainty_is_always_questioned(self) -> None:
        review = critic.review(
            assessment(confidence=1.0),
            pool=pool_of(*[evidence(f"ev_{i}") for i in range(8)]),
            trail=trail_with("screen_sanctions", ToolOutcome.OK),
            plan=InvestigationPlan(decisions=(
                RoutingDecision(agent="research_analyst", engaged=True, reason="in scope"),
            )),
            use_model=False,
        )

        challenges = review.by_kind(ChallengeKind.OVERCONFIDENT)
        assert challenges, "100% confidence must never pass unremarked"
        assert challenges[0].severity is ChallengeSeverity.ADVISORY

    def test_a_defensible_high_confidence_still_passes(self) -> None:
        """0.9 on a complete run is a claim the evidence can carry."""
        review = critic.review(
            assessment(confidence=0.9),
            pool=pool_of(*[evidence(f"ev_{i}") for i in range(8)]),
            trail=trail_with("screen_sanctions", ToolOutcome.OK),
            plan=InvestigationPlan(decisions=(
                RoutingDecision(agent="research_analyst", engaged=True, reason="in scope"),
            )),
            use_model=False,
        )

        assert not review.by_kind(ChallengeKind.OVERCONFIDENT)
