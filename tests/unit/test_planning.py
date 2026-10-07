"""Routing rules.

These are the "deterministic seams" §6.2 says should be rules rather than model
calls, so they get exhaustive tests rather than a sample. Each rule answers a
question with a determinate answer, and every decision must carry a reason —
that is what M-02's "routing decisions are logged" actually requires.
"""

from __future__ import annotations

import pytest

from sentinel.domain.planning import InvestigationPlan, RoutingDecision
from sentinel.services.planning import (
    MEMORY,
    COMPLIANCE,
    FINANCIAL,
    NEWS,
    RESEARCH,
    SPECIALISTS,
    plan_investigation,
    route_from_plan,
)


class FakeSettings:
    def __init__(self, tavily: str = "tvly-key") -> None:
        self.TAVILY_API_KEY = tavily


class TestRoutingDecision:
    def test_a_decision_without_a_reason_is_refused(self) -> None:
        """An unexplained branch is exactly what the objective asks to be logged."""
        with pytest.raises(ValueError, match="reason"):
            RoutingDecision(agent=FINANCIAL, engaged=False, reason="   ")


class TestPlanInvestigation:
    def test_a_named_listed_entity_engages_every_specialist(self) -> None:
        plan = plan_investigation("Alphabet Inc.", "GOOGL", settings=FakeSettings())

        assert plan.strategy == "full"
        assert set(plan.engaged) == set(SPECIALISTS)
        assert plan.is_complete

    def test_no_ticker_skips_the_financial_specialist(self) -> None:
        """Rosneft has no US listing; yfinance has nothing to return."""
        plan = plan_investigation("Rosneft", "", settings=FakeSettings())

        assert plan.strategy == "reduced"
        assert FINANCIAL not in plan.engaged
        assert set(plan.engaged) == {RESEARCH, NEWS, COMPLIANCE}

    def test_the_skip_explains_itself(self) -> None:
        plan = plan_investigation("Rosneft", "", settings=FakeSettings())
        decision = plan.decision_for(FINANCIAL)

        assert decision is not None
        assert not decision.engaged
        assert "ticker" in decision.reason.lower()

    def test_a_missing_search_key_skips_osint_but_nothing_else(self) -> None:
        plan = plan_investigation("Acme Ltd", "ACME", settings=FakeSettings(tavily=""))

        assert NEWS not in plan.engaged
        assert {RESEARCH, FINANCIAL, COMPLIANCE} <= set(plan.engaged)
        assert "TAVILY_API_KEY" in (plan.decision_for(NEWS) or RoutingDecision(NEWS, False, "x")).reason

    def test_an_empty_subject_engages_nobody(self) -> None:
        plan = plan_investigation("", "", settings=FakeSettings())

        assert plan.engaged == ()
        assert len(plan.skipped) == 4

    def test_compliance_never_depends_on_an_api_key(self) -> None:
        """Screening reads a local list, so it must survive with no keys at all."""
        plan = plan_investigation("Acme Ltd", "", settings=FakeSettings(tavily=""))

        assert COMPLIANCE in plan.engaged

    def test_every_decision_carries_a_reason(self) -> None:
        for subject, ticker in [("Acme", "ACME"), ("Acme", ""), ("", "")]:
            plan = plan_investigation(subject, ticker, settings=FakeSettings())
            assert all(d.reason.strip() for d in plan.decisions)

    def test_whitespace_is_not_a_subject(self) -> None:
        assert plan_investigation("   ", "  ", settings=FakeSettings()).engaged == ()


class TestSummary:
    def test_a_full_plan_says_so_briefly(self) -> None:
        summary = plan_investigation("Acme", "ACME", settings=FakeSettings()).summary()
        assert "all 4" in summary

    def test_a_reduced_plan_names_what_was_skipped_and_why(self) -> None:
        summary = plan_investigation("Rosneft", "", settings=FakeSettings()).summary()

        assert FINANCIAL in summary
        assert "ticker" in summary.lower()


class TestRouteFromPlan:
    def test_routes_to_the_engaged_specialists(self) -> None:
        plan = plan_investigation("Alphabet Inc.", "GOOGL", settings=FakeSettings())
        assert set(route_from_plan(plan.to_dict())) == {*SPECIALISTS, MEMORY}

    def test_memory_joins_every_run_that_has_a_subject(self) -> None:
        """It queries the local ledger, so there is no cost reason to skip it."""
        plan = plan_investigation("Rosneft", "", settings=FakeSettings())
        assert MEMORY in route_from_plan(plan.to_dict())

    def test_memory_is_skipped_when_no_specialist_qualifies(self) -> None:
        plan = plan_investigation("", "", settings=FakeSettings())
        assert MEMORY not in route_from_plan(plan.to_dict())

    def test_omits_the_skipped_specialist(self) -> None:
        plan = plan_investigation("Rosneft", "", settings=FakeSettings())
        assert FINANCIAL not in route_from_plan(plan.to_dict())

    def test_falls_through_to_the_supervisor_when_nobody_qualifies(self) -> None:
        """An empty target list would strand the graph with no node to run."""
        plan = plan_investigation("", "", settings=FakeSettings())
        assert route_from_plan(plan.to_dict()) == ["supervisor"]

    def test_a_malformed_plan_does_not_strand_the_graph(self) -> None:
        assert route_from_plan({}) == ["supervisor"]
        assert route_from_plan({"decisions": "nonsense"}) == ["supervisor"]

    def test_unknown_agent_names_are_ignored(self) -> None:
        plan = InvestigationPlan(decisions=(
            RoutingDecision(agent="ghost_analyst", engaged=True, reason="not a real node"),
            RoutingDecision(agent=RESEARCH, engaged=True, reason="real"),
        ))
        assert route_from_plan(plan.to_dict()) == [RESEARCH, MEMORY]


class TestPlanSerialisation:
    def test_round_trips(self) -> None:
        original = plan_investigation("Rosneft", "", settings=FakeSettings())
        restored = InvestigationPlan.from_dict(original.to_dict())

        assert restored.engaged == original.engaged
        assert restored.strategy == original.strategy
