"""Contract tests for the collector nodes.

The specialists are tested here as graph nodes rather than as agents: what they
put into the shared state, and what they do when their tool does not deliver.
Both matter more than the prose they log.

Two properties are load-bearing and neither is visible from a passing run:

* **Reducer shape.** `evidence`, `tool_calls` and `logs` are declared with
  `operator.add`, so every node must contribute a *list*. A node returning a
  bare dict or string would fail only under the parallel fan-out, and only
  sometimes.
* **Degradation.** A tool that did not deliver must leave no evidence behind. A
  node that files its tool's failure text as findings is how "no adverse media
  found" comes to mean "nothing was ever searched" — the defect the four-state
  tool classification was introduced to end.

No network and no model: the registry is replaced with a stub whose outcome each
test chooses.
"""

from __future__ import annotations

import pytest

from sentinel.domain.tooling import ToolInvocation, ToolOutcome
from sentinel.graph import nodes
from sentinel.tools.registry import ToolResult

REDUCER_CHANNELS = ("evidence", "tool_calls", "logs")


class StubRegistry:
    """Returns a scripted result per tool name and records how it was called."""

    def __init__(self, responses: dict) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, name, *, caller, trail=None, **kwargs):
        self.calls.append((name, kwargs))
        entry = self._responses.get(name, ({}, ToolOutcome.OK))
        data, outcome = entry[0], entry[1]
        detail = entry[2] if len(entry) > 2 else f"{name} reported {outcome.value}"

        # Mirrors ToolRegistry._record: a digest describes a run that produced an
        # answer (OK or EMPTY); `error` is populated only when the tool failed or
        # never ran. Getting this backwards in the stub would let a node pass a
        # test it fails in production.
        failed = outcome in (ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE)
        invocation = ToolInvocation(
            tool=name,
            caller=caller,
            outcome=outcome,
            arguments=kwargs,
            duration_ms=1.0,
            result_digest="" if failed else detail,
            error=detail if failed else "",
        )
        if trail is not None:
            trail.add(invocation)
        return ToolResult(data=data, invocation=invocation)


@pytest.fixture
def registry(monkeypatch):
    def install(responses: dict) -> StubRegistry:
        stub = StubRegistry(responses)
        monkeypatch.setattr(nodes, "get_registry", lambda: stub)
        return stub

    return install


def assert_reducer_safe(update: dict) -> None:
    for channel in REDUCER_CHANNELS:
        if channel in update:
            assert isinstance(update[channel], list), (
                f"{channel!r} must be a list: the channel merges parallel "
                f"branches with operator.add, got {type(update[channel]).__name__}"
            )


class TestFinancialAnalystNode:
    """The node previously defaulted to a fixed ticker when none was supplied."""

    def test_no_ticker_files_no_market_data(self, registry):
        stub = registry({})
        update = nodes.financial_analyst_node({"subject_name": "Acme Trading Limited"})

        assert stub.calls == [], "no ticker means there is nothing to look up"
        assert not update.get("financial_data")
        assert not update.get("evidence")

    def test_no_ticker_says_why_it_was_skipped(self, registry):
        registry({})
        update = nodes.financial_analyst_node({"subject_name": "Acme Trading Limited"})
        assert "no ticker" in " ".join(update["logs"]).lower()

    def test_no_ticker_never_substitutes_another_company(self, registry):
        """Filing one issuer's metrics as evidence about another is the worst case."""
        stub = registry({})
        nodes.financial_analyst_node({"subject_name": "Acme Trading Limited", "ticker": ""})
        assert not any(kwargs.get("ticker") for _, kwargs in stub.calls)

    def test_whitespace_ticker_is_not_a_ticker(self, registry):
        stub = registry({})
        nodes.financial_analyst_node({"subject_name": "Acme", "ticker": "   "})
        assert stub.calls == []

    def test_supplied_ticker_reaches_the_tool(self, registry):
        stub = registry({"fetch_company_financials": ({"symbol": "WOLF"}, ToolOutcome.OK)})
        nodes.financial_analyst_node({"subject_name": "Wolfspeed", "ticker": "WOLF"})

        assert stub.calls[0][0] == "fetch_company_financials"
        assert stub.calls[0][1]["ticker"] == "WOLF"

    def test_unavailable_feed_leaves_no_evidence(self, registry):
        registry({"fetch_company_financials": ({}, ToolOutcome.UNAVAILABLE)})
        update = nodes.financial_analyst_node({"ticker": "WOLF"})
        assert update.get("evidence") == []

    def test_update_is_reducer_safe(self, registry):
        registry({"fetch_company_financials": ({"symbol": "WOLF"}, ToolOutcome.OK)})
        assert_reducer_safe(nodes.financial_analyst_node({"ticker": "WOLF"}))


class TestNewsAnalystNode:
    def test_failed_search_is_not_stored_as_reporting(self, registry):
        """The tool reports its own errors as ordinary strings."""
        warning = "OSINT Warning: TAVILY_API_KEY missing."
        registry({"search_company_news": (warning, ToolOutcome.UNAVAILABLE)})

        update = nodes.news_analyst_node({"subject_name": "Wolfspeed"})

        assert update["evidence"] == []
        assert warning not in str(update["news_data"])

    def test_failed_search_is_reported_in_the_log(self, registry):
        registry({"search_company_news": ("", ToolOutcome.UNAVAILABLE)})
        update = nodes.news_analyst_node({"subject_name": "Wolfspeed"})
        assert "no open-source reporting collected" in " ".join(update["logs"])

    def test_successful_search_yields_citable_records(self, registry):
        hits = [
            {"title": "Chapter 11 filing", "url": "https://example.test/a", "content": "..."},
            {"title": "Delisting notice", "url": "https://example.test/b", "content": "..."},
        ]
        registry({"search_company_news": (str(hits), ToolOutcome.OK)})

        update = nodes.news_analyst_node({"subject_name": "Wolfspeed"})
        assert len(update["evidence"]) == 2

    def test_update_is_reducer_safe(self, registry):
        registry({"search_company_news": ("[]", ToolOutcome.OK)})
        assert_reducer_safe(nodes.news_analyst_node({"subject_name": "Wolfspeed"}))


class TestOsintParsing:
    def test_reads_back_a_stringified_list(self):
        assert nodes._parse_osint("[{'title': 'x'}]") == [{"title": "x"}]

    def test_passes_a_real_list_through(self):
        assert nodes._parse_osint([{"title": "x"}]) == [{"title": "x"}]

    @pytest.mark.parametrize("junk", ["", None, "not a list", "[unclosed", "{}", "42"])
    def test_unparseable_output_yields_no_records_instead_of_raising(self, junk):
        assert nodes._parse_osint(junk) == []

    def test_non_dict_entries_are_discarded(self):
        assert nodes._parse_osint("['a', {'title': 'x'}, 3]") == [{"title": "x"}]


class TestComplianceAnalystNode:
    def test_both_halves_of_the_screen_run(self, registry):
        stub = registry({
            "build_compliance_brief": ({"obligations": [], "sources": []}, ToolOutcome.OK),
            "screen_sanctions": ({"status": "CLEAR", "matches": []}, ToolOutcome.OK),
        })
        nodes.compliance_analyst_node({"subject_name": "Wolfspeed"})

        assert {name for name, _ in stub.calls} == {"build_compliance_brief", "screen_sanctions"}

    def test_screening_status_is_surfaced_verbatim(self, registry):
        registry({
            "build_compliance_brief": ({"obligations": [], "sources": []}, ToolOutcome.OK),
            "screen_sanctions": ({"status": "UNAVAILABLE", "matches": []}, ToolOutcome.UNAVAILABLE),
        })
        update = nodes.compliance_analyst_node({"subject_name": "Wolfspeed"})
        assert "UNAVAILABLE" in " ".join(update["logs"])

    def test_the_subject_is_what_gets_screened(self, registry):
        stub = registry({
            "build_compliance_brief": ({"obligations": [], "sources": []}, ToolOutcome.OK),
            "screen_sanctions": ({"status": "CLEAR", "matches": []}, ToolOutcome.OK),
        })
        nodes.compliance_analyst_node({"subject_name": "Bank Melli Iran"})

        screening = next(kwargs for name, kwargs in stub.calls if name == "screen_sanctions")
        assert screening["subject_name"] == "Bank Melli Iran"

    def test_update_is_reducer_safe(self, registry):
        registry({
            "build_compliance_brief": ({"obligations": [], "sources": []}, ToolOutcome.OK),
            "screen_sanctions": ({"status": "CLEAR", "matches": []}, ToolOutcome.OK),
        })
        assert_reducer_safe(nodes.compliance_analyst_node({"subject_name": "Wolfspeed"}))


@pytest.fixture
def offline_baseline(monkeypatch):
    """The baseline is a model call, not a tool call, so the registry stub misses it.

    It reaches the gateway directly — which is deliberate (the registry audits
    tools; the gateway accounts for models) but means this node would otherwise
    put the fast suite on the network.
    """
    monkeypatch.setattr(
        nodes,
        "fetch_entity_baseline",
        lambda subject_name, ticker="": {
            "entity_name": subject_name,
            "ticker": ticker.upper() if ticker else "N/A",
            "business_summary": "Stubbed baseline.",
        },
    )


class TestResearchAnalystNode:
    def test_unmatched_registrant_is_reported_not_invented(self, registry, offline_baseline):
        registry({"fetch_sec_filings": (
            {"matched": False, "reason": "No SEC registrant matched."},
            ToolOutcome.EMPTY,
            "No SEC registrant matched.",
        )})
        update = nodes.research_analyst_node({"subject_name": "Acme Trading Limited"})

        assert update["edgar_data"]["matched"] is False
        assert "No SEC registrant" in str(update["logs"])

    def test_matched_registrant_reports_its_cik(self, registry, offline_baseline):
        registry({"fetch_sec_filings": (
            {"matched": True, "cik": "0000895419", "flags": ["bankruptcy"], "filings_reviewed": 1000},
            ToolOutcome.OK,
        )})
        update = nodes.research_analyst_node({"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert "0000895419" in " ".join(update["logs"])

    def test_the_subject_reaches_the_filing_lookup(self, registry, offline_baseline):
        stub = registry({"fetch_sec_filings": ({"matched": False, "reason": "none"}, ToolOutcome.EMPTY)})
        nodes.research_analyst_node({"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert stub.calls[0][1]["subject_name"] == "Wolfspeed"

    def test_update_is_reducer_safe(self, registry, offline_baseline):
        registry({"fetch_sec_filings": ({"matched": False, "reason": "none"}, ToolOutcome.EMPTY)})
        assert_reducer_safe(nodes.research_analyst_node({"subject_name": "Acme"}))


class TestIntakeNode:
    def test_a_plan_is_recorded_before_any_specialist_runs(self):
        update = nodes.intake_node({"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert update["plan"]["decisions"]

    def test_every_routing_decision_carries_a_reason(self):
        update = nodes.intake_node({"subject_name": "Wolfspeed", "ticker": "WOLF"})
        assert all(d["reason"].strip() for d in update["plan"]["decisions"])

    def test_an_empty_request_still_produces_a_plan(self):
        update = nodes.intake_node({})
        assert update["plan"]["decisions"]
        assert all(not d["engaged"] for d in update["plan"]["decisions"])

    def test_update_is_reducer_safe(self):
        assert_reducer_safe(nodes.intake_node({"subject_name": "Wolfspeed", "ticker": "WOLF"}))
