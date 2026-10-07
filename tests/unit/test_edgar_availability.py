"""Tests for the one distinction the filing collector has to get right.

`matched: False` was answering two different questions with one word:

* *"This entity is not an SEC registrant."* A finding about the entity. A
  foreign or private company genuinely has no EDGAR history, and a verdict may
  legitimately rest on that.
* *"EDGAR could not be reached."* A finding about the run. It establishes
  nothing, and a verdict may rest on none of it.

Both produced `EMPTY` — "ran, found nothing" — so an unreachable SEC endpoint
was indistinguishable from a clean filing history. That is §2.7's EMPTY /
UNAVAILABLE confusion, and it survived in the collector where it matters most:
the SEC rate-limits by User-Agent, so the unreachable path is hit in ordinary
operation, not only during an outage.

No network: the two HTTP helpers are replaced.
"""

from __future__ import annotations

import pytest

from sentinel.domain.tooling import ToolOutcome
from sentinel.tools import edgar
from sentinel.tools.registry import classify_edgar

INDEX = {
    "by_ticker": {"WOLF": {"cik": "0000895419", "ticker": "WOLF", "title": "Wolfspeed, Inc."}},
    "by_name": {"wolfspeed": {"cik": "0000895419", "ticker": "WOLF", "title": "Wolfspeed, Inc."}},
}
EMPTY_INDEX = {"by_ticker": {}, "by_name": {}}

SUBMISSIONS = {
    "name": "Wolfspeed, Inc.",
    "sic": "3674",
    "exchanges": ["NYSE"],
    "filings": {"recent": {"form": [], "filingDate": [], "items": [], "accessionNumber": []}},
}


@pytest.fixture
def edgar_env(monkeypatch):
    """Installs a ticker index and a submissions response of the test's choosing."""

    def install(*, index=INDEX, submissions=SUBMISSIONS):
        monkeypatch.setattr(edgar, "_ticker_index", lambda: index)
        monkeypatch.setattr(edgar, "_get_json", lambda url: submissions)

    return install


class TestUnreachableIsNotEmpty:
    def test_a_failed_index_fetch_is_unavailable(self, edgar_env):
        edgar_env(index=EMPTY_INDEX)
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert classify_edgar(profile)[0] is ToolOutcome.UNAVAILABLE

    def test_a_failed_index_fetch_does_not_claim_the_entity_is_unregistered(self, edgar_env):
        edgar_env(index=EMPTY_INDEX)
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert "not a finding about the entity" in profile["reason"]

    def test_an_unreachable_submissions_endpoint_is_unavailable(self, edgar_env):
        edgar_env(submissions=None)
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert classify_edgar(profile)[0] is ToolOutcome.UNAVAILABLE

    def test_an_unreachable_submissions_endpoint_says_the_history_was_not_read(self, edgar_env):
        edgar_env(submissions=None)
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert "never read" in profile["reason"]

    def test_a_malformed_submissions_payload_is_unavailable(self, edgar_env):
        edgar_env(submissions="<html>rate limited</html>")
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert classify_edgar(profile)[0] is ToolOutcome.UNAVAILABLE


class TestGenuinelyUnregisteredIsStillEmpty:
    """The distinction must not swallow the real finding it exists beside."""

    def test_an_unlisted_entity_is_empty_not_unavailable(self, edgar_env):
        edgar_env()
        profile = edgar.fetch_edgar_profile("Acme Trading Limited")
        assert classify_edgar(profile)[0] is ToolOutcome.EMPTY

    def test_an_unlisted_entity_is_marked_reachable(self, edgar_env):
        edgar_env()
        profile = edgar.fetch_edgar_profile("Acme Trading Limited")
        assert profile["reachable"] is True

    def test_the_reason_explains_the_index_coverage(self, edgar_env):
        edgar_env()
        profile = edgar.fetch_edgar_profile("Acme Trading Limited")
        assert "listed US issuers" in profile["reason"]


class TestMatchedRegistrant:
    def test_a_resolved_registrant_is_ok(self, edgar_env):
        edgar_env()
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert classify_edgar(profile)[0] is ToolOutcome.OK

    def test_the_cik_is_carried_through(self, edgar_env):
        edgar_env()
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert profile["cik"] == "0000895419"

    def test_resolution_falls_back_from_ticker_to_name(self, edgar_env):
        edgar_env()
        profile = edgar.fetch_edgar_profile("Wolfspeed")
        assert profile["matched"] is True


class TestIndexCacheRecovers:
    """A throttled fetch must not disable the collector for the process lifetime.

    The index was memoised with `@lru_cache(maxsize=1)`, which cached the empty
    result of a failed fetch as readily as a good one. One rate-limited response
    and every later investigation lost its filing history until the API was
    restarted. It was caught in the running container: `fetch_sec_filings`
    reporting UNAVAILABLE in 0.06 ms, while the SEC answered a direct request
    with 10,407 registrants.
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        edgar._INDEX_CACHE["value"] = None
        edgar._INDEX_CACHE["retry_after"] = 0.0
        yield
        edgar._INDEX_CACHE["value"] = None
        edgar._INDEX_CACHE["retry_after"] = 0.0

    @pytest.fixture
    def clock(self, monkeypatch):
        """A controllable monotonic clock, so the cooldown can be stepped over."""
        state = {"now": 1_000.0}
        monkeypatch.setattr(edgar.time, "monotonic", lambda: state["now"])
        return state

    def test_a_failed_fetch_is_retried_once_the_cooldown_passes(self, monkeypatch, clock):
        calls = []

        def flaky(url):
            calls.append(url)
            return None if len(calls) == 1 else {"0": {"cik_str": 895419, "ticker": "WOLF", "title": "Wolfspeed, Inc."}}

        monkeypatch.setattr(edgar, "_get_json", flaky)

        assert edgar._ticker_index()["by_ticker"] == {}
        clock["now"] += edgar.INDEX_RETRY_COOLDOWN + 1
        assert "WOLF" in edgar._ticker_index()["by_ticker"]

    def test_the_cooldown_is_actually_observed(self, monkeypatch, clock):
        calls = []
        monkeypatch.setattr(edgar, "_get_json", lambda url: calls.append(url))

        edgar._ticker_index()
        clock["now"] += edgar.INDEX_RETRY_COOLDOWN - 1
        edgar._ticker_index()
        assert len(calls) == 1, "retried before the cooldown elapsed"

    def test_a_failure_is_not_retried_on_every_call(self, monkeypatch):
        """Retrying immediately would turn one throttle into a burst of them."""
        calls = []
        monkeypatch.setattr(edgar, "_get_json", lambda url: calls.append(url))

        for _ in range(5):
            edgar._ticker_index()
        assert len(calls) == 1

    def test_a_successful_fetch_is_only_fetched_once(self, monkeypatch):
        calls = []

        def ok(url):
            calls.append(url)
            return {"0": {"cik_str": 895419, "ticker": "WOLF", "title": "Wolfspeed, Inc."}}

        monkeypatch.setattr(edgar, "_get_json", ok)
        for _ in range(4):
            edgar._ticker_index()
        assert len(calls) == 1

    def test_an_empty_payload_counts_as_a_failure(self, monkeypatch):
        """An index with no registrants is a bad response, not a real answer."""
        monkeypatch.setattr(edgar, "_get_json", lambda url: {})
        edgar._ticker_index()
        assert edgar._INDEX_CACHE["value"] is None

    def test_recovery_restores_a_real_lookup_end_to_end(self, monkeypatch, clock):
        """The whole point: the collector comes back without a restart."""
        calls = []

        def flaky(url):
            calls.append(url)
            if len(calls) == 1:
                return None
            if "submissions" in url.lower():
                return SUBMISSIONS
            return {"0": {"cik_str": 895419, "ticker": "WOLF", "title": "Wolfspeed, Inc."}}

        monkeypatch.setattr(edgar, "_get_json", flaky)

        first = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert classify_edgar(first)[0] is ToolOutcome.UNAVAILABLE

        clock["now"] += edgar.INDEX_RETRY_COOLDOWN + 1
        second = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert classify_edgar(second)[0] is ToolOutcome.OK


class TestClassifierContract:
    def test_a_non_dict_result_is_a_failure(self):
        assert classify_edgar("not a dict")[0] is ToolOutcome.FAILED

    def test_every_outcome_carries_a_reason(self, edgar_env):
        for kwargs in ({"index": EMPTY_INDEX}, {"submissions": None}, {}):
            edgar_env(**kwargs)
            _, detail = classify_edgar(edgar.fetch_edgar_profile("Acme Trading Limited"))
            assert detail.strip()

    def test_unavailable_never_reports_supervisory_flags(self, edgar_env):
        """An unread history must not look like a clean one."""
        edgar_env(submissions=None)
        profile = edgar.fetch_edgar_profile("Wolfspeed", "WOLF")
        assert profile["flags"] == []
        assert profile["matched"] is False
