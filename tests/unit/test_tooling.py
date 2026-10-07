"""Tests for the tool-invocation records (M-04).

The audit trail is a compliance artefact, so the tests that matter most here are
not about happy paths. They are about the trail refusing to record something
misleading: an unexplained failure, a leaked key, or a summary that claims more
than the run achieved.
"""

from __future__ import annotations

import pytest

from sentinel.domain.tooling import (
    MAX_ARGUMENT_CHARS,
    REDACTED,
    ToolAuditTrail,
    ToolInvocation,
    ToolOutcome,
    redact_arguments,
)


def call(**overrides) -> ToolInvocation:
    payload = {
        "tool": "screen_sanctions",
        "caller": "compliance_analyst",
        "outcome": ToolOutcome.OK,
        "arguments": {"subject_name": "Rosneft"},
        "duration_ms": 40,
        "result_digest": "HIT: 1 match",
    }
    payload.update(overrides)
    return ToolInvocation(**payload)


class TestRedaction:
    def test_declared_secrets_are_masked(self) -> None:
        assert redact_arguments({"api_key": "sk-live-123"})["api_key"] == REDACTED

    def test_masking_matches_on_substring(self) -> None:
        """`groq_api_key` must be caught by the `api_key` rule without declaration."""
        out = redact_arguments({"groq_api_key": "sk-1", "TAVILY_TOKEN": "tv-2"})

        assert out["groq_api_key"] == REDACTED
        assert out["TAVILY_TOKEN"] == REDACTED

    def test_ordinary_arguments_survive(self) -> None:
        """A trail that drops its inputs cannot answer 'what did we screen?'."""
        assert redact_arguments({"subject_name": "Bank Melli"})["subject_name"] == "Bank Melli"

    def test_long_values_are_truncated(self) -> None:
        out = redact_arguments({"query": "x" * 5000})["query"]
        assert len(out) <= MAX_ARGUMENT_CHARS

    def test_numbers_keep_their_type(self) -> None:
        out = redact_arguments({"limit": 5, "strict": True, "cutoff": None})
        assert (out["limit"], out["strict"], out["cutoff"]) == (5, True, None)

    def test_extra_names_can_be_declared_per_tool(self) -> None:
        assert redact_arguments({"internal_ref": "abc"}, extra=("internal_ref",))["internal_ref"] == REDACTED


class TestInvocation:
    def test_a_call_must_name_its_tool(self) -> None:
        with pytest.raises(ValueError):
            call(tool="   ")

    @pytest.mark.parametrize("outcome", [ToolOutcome.FAILED, ToolOutcome.UNAVAILABLE])
    def test_an_unexplained_failure_is_rejected(self, outcome: ToolOutcome) -> None:
        """The whole point of the trail is that failures carry their reason."""
        with pytest.raises(ValueError, match="M-04"):
            call(outcome=outcome, error="")

    def test_empty_is_a_successful_outcome(self) -> None:
        """Finding nothing is an answer; it must not read as a fault."""
        assert call(outcome=ToolOutcome.EMPTY).ok

    def test_unavailable_is_not_a_successful_outcome(self) -> None:
        assert not call(outcome=ToolOutcome.UNAVAILABLE, error="no dataset").ok

    def test_one_line_carries_arguments_and_outcome(self) -> None:
        rendered = call().one_line()
        assert "screen_sanctions" in rendered and "Rosneft" in rendered and "ok" in rendered

    def test_roundtrip_preserves_the_record(self) -> None:
        original = call(evidence_ids=("ev_1", "ev_2"))
        restored = ToolInvocation.from_dict(original.to_dict())

        assert restored is not None
        assert (restored.tool, restored.arguments, restored.evidence_ids) == (
            original.tool, original.arguments, original.evidence_ids,
        )

    def test_a_malformed_row_is_skipped_not_raised(self) -> None:
        """One corrupt audit row must not make an investigation unreadable."""
        assert ToolInvocation.from_dict({"no": "tool"}) is None
        assert ToolInvocation.from_dict("not a dict") is None

    def test_a_failed_row_with_no_reason_is_readable_again(self) -> None:
        """The constructor forbids it, so reading one back must not explode."""
        restored = ToolInvocation.from_dict(
            {"tool": "x", "outcome": "failed", "error": ""}
        )
        assert restored is not None and restored.error == "No reason recorded."

    def test_an_unknown_outcome_does_not_break_the_read(self) -> None:
        restored = ToolInvocation.from_dict({"tool": "x", "outcome": "banana"})
        assert restored is not None and restored.outcome is ToolOutcome.OK


class TestAuditTrail:
    def test_summary_of_an_empty_trail_says_so(self) -> None:
        assert ToolAuditTrail().summary() == "No tools were called."

    def test_summary_keeps_the_three_counts_apart(self) -> None:
        """'All succeeded' would be false for a run where a lookup found nothing."""
        trail = ToolAuditTrail()
        trail.add(call())
        trail.add(call(tool="fetch_sec_filings", outcome=ToolOutcome.EMPTY))
        trail.add(call(tool="search_company_news", outcome=ToolOutcome.UNAVAILABLE, error="no key"))

        summary = trail.summary()

        assert "1 returned data" in summary
        assert "1 found nothing" in summary
        assert "1 did not run (search_company_news)" in summary

    def test_failures_exclude_empty_results(self) -> None:
        trail = ToolAuditTrail()
        trail.add(call(outcome=ToolOutcome.EMPTY))
        assert trail.failures == []

    def test_attribute_links_a_call_to_its_evidence(self) -> None:
        """This is what closes verdict -> factor -> evidence -> call."""
        trail = ToolAuditTrail()
        recorded = trail.add(call())

        trail.attribute(recorded, ["ev_1", "ev_2"])

        assert len(trail) == 1, "attribution must replace the record, not append a second"
        assert trail.invocations[0].evidence_ids == ("ev_1", "ev_2")

    def test_attributing_an_unrecorded_call_records_it(self) -> None:
        trail = ToolAuditTrail()
        trail.attribute(call(), ["ev_1"])
        assert len(trail) == 1

    def test_tools_used_is_deduplicated_in_call_order(self) -> None:
        trail = ToolAuditTrail()
        trail.add(call(tool="b"))
        trail.add(call(tool="a"))
        trail.add(call(tool="b"))

        assert trail.tools_used == ("b", "a")

    def test_total_duration_sums_the_calls(self) -> None:
        trail = ToolAuditTrail()
        trail.add(call(duration_ms=100))
        trail.add(call(duration_ms=250))
        assert trail.total_duration_ms == 350

    def test_rebuilding_from_state_drops_only_the_bad_rows(self) -> None:
        trail = ToolAuditTrail.from_dicts([call().to_dict(), {"junk": True}, call(tool="b").to_dict()])
        assert len(trail) == 2

    def test_rebuilding_from_a_non_list_is_empty_not_an_error(self) -> None:
        assert len(ToolAuditTrail.from_dicts(None)) == 0
