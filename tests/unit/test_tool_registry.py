"""Tests for the tool registry (M-04).

Every test here uses stub tools rather than the real five: the registry's job is
to call *something* uniformly, record it, and never let a fault escape. Binding
these tests to yfinance or EDGAR would make them slow and network-dependent
without testing anything more.

The classifier tests are the exception and the most valuable ones in the file —
they are the regression guards for tools that report their own failures as
ordinary-looking data.
"""

from __future__ import annotations

import pytest

from sentinel.domain.tooling import REDACTED, ToolAuditTrail, ToolOutcome
from sentinel.tools.registry import (
    ToolCategory,
    ToolRegistry,
    ToolSpec,
    classify_compliance_brief,
    classify_edgar,
    classify_financials,
    classify_news,
    classify_sanctions,
)


def ok_classifier(data):
    return ToolOutcome.OK, f"returned {data!r}"


def spec(**overrides) -> ToolSpec:
    payload = {
        "name": "stub",
        "summary": "A stub tool.",
        "category": ToolCategory.SCREENING,
        "fn": lambda **kwargs: {"received": kwargs},
        "classify": ok_classifier,
    }
    payload.update(overrides)
    return ToolSpec(**payload)


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(spec())
    return reg


class TestRegistration:
    def test_a_duplicate_name_is_refused(self) -> None:
        reg = ToolRegistry()
        reg.register(spec())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(spec())

    def test_an_unknown_tool_names_the_known_ones(self, registry: ToolRegistry) -> None:
        """A typo should be diagnosable from the error alone."""
        with pytest.raises(KeyError, match="stub"):
            registry.get("stbu")

    def test_the_catalogue_reports_availability(self) -> None:
        reg = ToolRegistry()
        reg.register(spec(availability=lambda: (False, "no dataset")))

        entry = reg.describe()[0]

        assert entry["available"] is False
        assert entry["unavailable_reason"] == "no dataset"

    def test_membership_and_size(self, registry: ToolRegistry) -> None:
        assert "stub" in registry and len(registry) == 1


class TestInvocation:
    def test_a_call_is_recorded_with_its_arguments(self, registry: ToolRegistry) -> None:
        trail = ToolAuditTrail()

        registry.invoke("stub", caller="research_analyst", trail=trail, subject_name="Rosneft")

        assert len(trail) == 1
        assert trail.invocations[0].arguments == {"subject_name": "Rosneft"}
        assert trail.invocations[0].caller == "research_analyst"

    def test_secrets_never_reach_the_trail(self, registry: ToolRegistry) -> None:
        trail = ToolAuditTrail()
        registry.invoke("stub", caller="x", trail=trail, api_key="sk-live-secret")

        assert trail.invocations[0].arguments["api_key"] == REDACTED

    def test_arguments_still_reach_the_tool_unredacted(self, registry: ToolRegistry) -> None:
        """Redaction is for the record, not the call."""
        result = registry.invoke("stub", caller="x", api_key="sk-live-secret")
        assert result.data["received"]["api_key"] == "sk-live-secret"

    def test_a_tool_that_raises_does_not_end_the_investigation(self) -> None:
        def explode(**_):
            raise RuntimeError("upstream 503")

        reg = ToolRegistry()
        reg.register(spec(fn=explode, fallback=lambda: {"status": "UNKNOWN"}))
        trail = ToolAuditTrail()

        result = reg.invoke("stub", caller="x", trail=trail)

        assert result.outcome is ToolOutcome.FAILED
        assert result.data == {"status": "UNKNOWN"}, "caller gets the declared shape"
        assert "upstream 503" in trail.invocations[0].error

    def test_an_unavailable_tool_is_never_called(self) -> None:
        """A missing key is knowable before the call, not after it fails."""
        calls = []
        reg = ToolRegistry()
        reg.register(spec(
            fn=lambda **kwargs: calls.append(kwargs),
            availability=lambda: (False, "TAVILY_API_KEY is not set"),
        ))

        result = reg.invoke("stub", caller="x")

        assert calls == []
        assert result.outcome is ToolOutcome.UNAVAILABLE
        assert "TAVILY_API_KEY" in result.reason

    def test_a_broken_availability_probe_reports_unavailable(self) -> None:
        def broken():
            raise OSError("disk gone")

        reg = ToolRegistry()
        reg.register(spec(availability=broken))

        assert reg.invoke("stub", caller="x").outcome is ToolOutcome.UNAVAILABLE

    def test_a_broken_classifier_does_not_discard_a_good_result(self) -> None:
        def broken(_data):
            raise KeyError("bad classifier")

        reg = ToolRegistry()
        reg.register(spec(classify=broken))

        result = reg.invoke("stub", caller="x")

        assert result.ok, "a bug in the classifier must not fail the tool"
        assert result.data is not None

    def test_a_classifier_that_fails_without_a_reason_gets_one(self) -> None:
        """The invocation constructor would otherwise refuse to record it."""
        reg = ToolRegistry()
        reg.register(spec(classify=lambda _d: (ToolOutcome.FAILED, "   ")))

        assert "without a reason" in reg.invoke("stub", caller="x").reason

    def test_an_unregistered_name_raises(self, registry: ToolRegistry) -> None:
        """A runtime fault degrades; a programming error must fail loudly."""
        with pytest.raises(KeyError):
            registry.invoke("nope", caller="x")

    def test_a_trail_is_optional(self, registry: ToolRegistry) -> None:
        assert registry.invoke("stub", caller="x").ok

    def test_duration_is_recorded(self, registry: ToolRegistry) -> None:
        assert registry.invoke("stub", caller="x").invocation.duration_ms >= 0


class TestClassifiers:
    """The regression guards. Each one encodes a way a tool lies about failing."""

    def test_a_missing_search_key_is_not_reporting(self) -> None:
        """`search_company_news` returns its own error as a plain string.

        Before this classifier the warning text was stored as news_data and read
        downstream as though it described the entity.
        """
        outcome, detail = classify_news("OSINT Warning: TAVILY_API_KEY missing. Evaluated query: 'x'.")

        assert outcome is ToolOutcome.UNAVAILABLE
        assert "TAVILY_API_KEY" in detail

    def test_a_failed_search_is_a_failure_not_an_empty_result(self) -> None:
        assert classify_news("News search failed: timeout")[0] is ToolOutcome.FAILED

    def test_no_results_is_empty(self) -> None:
        assert classify_news("[]")[0] is ToolOutcome.EMPTY

    def test_real_reporting_is_ok(self) -> None:
        assert classify_news("[{'title': 'Reuters report'}]")[0] is ToolOutcome.OK

    def test_a_clean_screen_is_a_real_answer(self) -> None:
        outcome, detail = classify_sanctions({
            "screened": True, "status": "CLEAR", "matches": [],
            "providers": [{"available": True}, {"available": True}],
        })

        assert outcome is ToolOutcome.OK
        assert "2/2 providers" in detail

    def test_partial_coverage_is_recorded_in_the_digest(self) -> None:
        """A CLEAR from one list is a weaker statement than from two."""
        _, detail = classify_sanctions({
            "screened": True, "status": "CLEAR", "matches": [],
            "providers": [{"available": True}, {"available": False}],
        })

        assert "1/2 providers" in detail

    def test_a_screening_where_nothing_ran_is_unavailable(self) -> None:
        """The dangerous case: no list was consulted, yet nothing was flagged."""
        outcome, _ = classify_sanctions({
            "screened": False, "status": "UNAVAILABLE",
            "providers": [{"available": False, "label": "OFAC", "detail": "dataset missing"}],
        })

        assert outcome is ToolOutcome.UNAVAILABLE

    def test_a_hit_is_ok_with_a_match_count(self) -> None:
        outcome, detail = classify_sanctions({
            "screened": True, "status": "HIT", "matches": [{}, {}],
            "providers": [{"available": True}],
        })

        assert outcome is ToolOutcome.OK and "2 match(es)" in detail

    def test_not_being_an_sec_registrant_is_a_finding(self) -> None:
        """Private and foreign entities are legitimately absent from EDGAR."""
        outcome, detail = classify_edgar({"matched": False, "reason": "No SEC registrant matched."})

        assert outcome is ToolOutcome.EMPTY
        assert "No SEC registrant" in detail

    def test_a_matched_registrant_reports_its_cik(self) -> None:
        outcome, detail = classify_edgar({"matched": True, "cik": "0000895419", "flags": ["a", "b"]})

        assert outcome is ToolOutcome.OK and "0000895419" in detail

    def test_a_financials_error_field_is_a_failure(self) -> None:
        assert classify_financials({"error": "no such ticker"})[0] is ToolOutcome.FAILED

    def test_all_metrics_unavailable_is_empty(self) -> None:
        outcome, _ = classify_financials({
            "symbol": "XYZ", "market_cap": "N/A", "pe_ratio": "N/A",
            "total_debt": "N/A", "free_cashflow": "N/A", "profit_margins": "N/A",
        })

        assert outcome is ToolOutcome.EMPTY

    def test_partial_metrics_are_still_usable(self) -> None:
        outcome, detail = classify_financials({
            "symbol": "MSFT", "company_name": "Microsoft", "market_cap": 3e12,
            "pe_ratio": "N/A", "total_debt": 1e11, "free_cashflow": "N/A",
            "profit_margins": 0.36,
        })

        assert outcome is ToolOutcome.OK and "3/5 metrics" in detail

    def test_an_unindexed_corpus_is_unavailable_not_empty(self) -> None:
        """ADR-005: this exact confusion left RAG silently broken for two weeks."""
        outcome, _ = classify_compliance_brief({
            "retrieval": "unavailable", "obligations": [], "sources": [],
            "error": "No compliance passages could be retrieved.",
        })

        assert outcome is ToolOutcome.UNAVAILABLE

    def test_retrieval_without_synthesis_is_degraded_but_ran(self) -> None:
        outcome, detail = classify_compliance_brief({
            "retrieval": "frameworks", "obligations": [], "sources": [{}, {}],
            "error": "model returned invalid JSON",
        })

        assert outcome is ToolOutcome.EMPTY
        assert "2 passages" in detail

    def test_a_good_brief_counts_obligations_and_passages(self) -> None:
        outcome, detail = classify_compliance_brief({
            "retrieval": "frameworks", "obligations": [{}, {}, {}],
            "sources": [{}, {}, {}, {}], "error": "",
        })

        assert outcome is ToolOutcome.OK
        assert "3 obligation(s)" in detail and "4 passage(s)" in detail

    @pytest.mark.parametrize(
        "classifier",
        [classify_sanctions, classify_edgar, classify_financials, classify_compliance_brief],
    )
    def test_a_non_dict_result_is_a_failure_not_a_crash(self, classifier) -> None:
        assert classifier("unexpected string")[0] is ToolOutcome.FAILED


class TestDefaultRegistry:
    def test_every_tool_is_registered(self) -> None:
        from sentinel.tools.registry import build_default_registry

        assert set(build_default_registry().names) == {
            # External sources
            "screen_sanctions",
            "fetch_sec_filings",
            "fetch_company_financials",
            "search_company_news",
            "build_compliance_brief",
            "recall_entity_history",
            # Derivations over evidence already collected. Registered so the
            # audit trail covers the whole pipeline rather than only the calls
            # that leave the process.
            "assess_fraud_signals",
            "resolve_entity_network",
            "review_assessment",
        }

    def test_every_tool_declares_where_its_data_comes_from(self) -> None:
        """The catalogue is read by an operator deciding whether to trust a run."""
        from sentinel.tools.registry import build_default_registry

        for entry in build_default_registry().describe():
            assert entry["data_source"], f"{entry['name']} does not name its data source"
            assert entry["summary"].endswith("."), f"{entry['name']} has no readable summary"
