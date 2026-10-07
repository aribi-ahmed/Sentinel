"""Tests for the tool-output → `Evidence` translation.

This layer is where a wrong confidence level would quietly corrupt the verdict:
if a press article were filed as HIGH confidence alongside an SEC filing, the
scorer would treat rumour and disclosure as equals. So the confidence rules get
explicit tests, not just the happy path.
"""

from __future__ import annotations

from sentinel.domain import Confidence, EvidenceKind
from sentinel.services import evidence_builder as builder


class TestBaseline:
    def test_produces_one_low_confidence_record(self) -> None:
        evidence = builder.from_baseline(
            {"entity_name": "Alphabet Inc.", "ticker": "GOOGL", "business_summary": "A technology holding company."},
            "Alphabet Inc.",
        )

        assert len(evidence) == 1
        # An LLM-written summary is interpretation, not a primary record.
        assert evidence[0].confidence is Confidence.LOW
        assert evidence[0].kind is EvidenceKind.BASELINE

    def test_ignores_an_empty_summary(self) -> None:
        assert builder.from_baseline({"business_summary": "   "}, "Acme") == []

    def test_ignores_a_non_dict(self) -> None:
        assert builder.from_baseline("not a dict", "Acme") == []


class TestEdgar:
    def profile(self, **overrides) -> dict:
        return {
            "matched": True,
            "company": "WOLFSPEED, INC.",
            "cik": "0000895419",
            "sic_description": "Semiconductors",
            "state_of_incorporation": "DE",
            "filings_reviewed": 1000,
            "source_url": "https://sec.gov/x",
            "flags": [
                {"label": "Bankruptcy or receivership", "date": "2025-07-01",
                 "form": "8-K", "code": "1.03", "severity": "severe"},
            ],
            **overrides,
        }

    def test_registration_plus_one_record_per_flag(self) -> None:
        evidence = builder.from_edgar(self.profile())

        assert len(evidence) == 2
        assert all(item.kind is EvidenceKind.FILING for item in evidence)

    def test_filings_are_high_confidence_regardless_of_coverage(self) -> None:
        """A disclosure is a matter of record — that is the whole point of EDGAR."""
        evidence = builder.from_edgar(self.profile())

        assert all(item.confidence is Confidence.HIGH for item in evidence)

    def test_flag_metadata_is_preserved_for_the_ui(self) -> None:
        flag = builder.from_edgar(self.profile())[1]

        assert flag.metadata["code"] == "1.03"
        assert flag.metadata["severity"] == "severe"

    def test_an_unmatched_registrant_yields_nothing(self) -> None:
        assert builder.from_edgar({"matched": False, "reason": "non-US"}) == []


class TestFinancials:
    def test_one_record_per_available_metric(self) -> None:
        evidence = builder.from_financials(
            {"symbol": "GOOGL", "market_cap": 4.17e12, "pe_ratio": 17.1,
             "total_debt": 1.2e11, "free_cashflow": 2.2e10, "profit_margins": 0.5477},
            "GOOGL",
        )

        assert len(evidence) == 5
        assert all(item.kind is EvidenceKind.MARKET_DATA for item in evidence)

    def test_skips_metrics_yfinance_could_not_supply(self) -> None:
        evidence = builder.from_financials({"symbol": "X", "market_cap": 1e9, "pe_ratio": "N/A"}, "X")

        assert len(evidence) == 1
        assert "Market capitalisation" in evidence[0].summary

    def test_large_numbers_are_rendered_readably(self) -> None:
        [record] = builder.from_financials({"symbol": "X", "market_cap": 4.17e12}, "X")
        assert "4.17T" in record.summary

    def test_percentages_are_rendered_as_percentages(self) -> None:
        [record] = builder.from_financials({"symbol": "X", "profit_margins": 0.5477}, "X")
        assert "54.8%" in record.summary

    def test_a_tool_error_yields_nothing(self) -> None:
        assert builder.from_financials({"error": "Failed to fetch"}, "X") == []


class TestOsint:
    def test_high_relevance_is_medium_confidence_at_best(self) -> None:
        """Coverage is never a finding, so open-source tops out below a filing."""
        [record] = builder.from_osint([
            {"title": "Antitrust probe", "url": "https://www.reuters.com/a", "content": "…", "score": 0.95},
        ])

        assert record.confidence is Confidence.MEDIUM
        assert record.confidence.weight < Confidence.HIGH.weight

    def test_weak_relevance_drops_to_low(self) -> None:
        [record] = builder.from_osint([
            {"title": "Opinion piece", "url": "https://blog.example.com/a", "score": 0.31},
        ])

        assert record.confidence is Confidence.LOW

    def test_source_is_the_publishing_host(self) -> None:
        [record] = builder.from_osint([{"title": "T", "url": "https://www.tradingview.com/news/x"}])
        assert record.source == "tradingview.com"

    def test_items_without_a_title_or_url_are_dropped(self) -> None:
        assert builder.from_osint([{"content": "orphaned text"}]) == []


class TestCompliance:
    def test_each_obligation_cites_its_framework_and_page(self) -> None:
        [record] = builder.from_compliance({
            "obligations": [
                {"control": "Board Compliance Oversight", "framework": "DOJ ECCP (2024)",
                 "page": 11, "requirement": "Ensure the board includes compliance expertise.",
                 "severity": "core"},
            ]
        })

        assert record.kind is EvidenceKind.POLICY
        assert "DOJ ECCP (2024), p.11" == record.source
        assert record.metadata["severity"] == "core"

    def test_an_empty_brief_yields_nothing(self) -> None:
        assert builder.from_compliance({"obligations": []}) == []


class TestSanctions:
    def test_a_clean_screen_is_itself_evidence(self) -> None:
        """Without this, the verdict could not say 'no exposure' — only stay silent."""
        [record] = builder.from_sanctions({
            "screened": True, "status": "CLEAR", "subject": "Alphabet Inc.",
            "list_size": 19199, "matches": [],
            "providers": [{"label": "OFAC SDN (local)", "available": True}],
        })

        assert record.kind is EvidenceKind.WATCHLIST
        assert record.confidence is Confidence.HIGH
        assert "19,199" in record.detail

    def test_a_hit_produces_one_record_per_match(self) -> None:
        evidence = builder.from_sanctions({
            "screened": True, "status": "HIT", "subject": "Rosneft", "list_size": 19199,
            "matches": [
                {"name": "ROSNEFT OIL COMPANY", "program": "RUSSIA-EO14024",
                 "matched_on": "alias", "confidence": 1.0},
            ],
            "providers": [{"label": "OFAC SDN (local)", "available": True}],
        })

        assert len(evidence) == 1
        assert evidence[0].confidence is Confidence.HIGH
        assert evidence[0].metadata["program"] == "RUSSIA-EO14024"

    def test_a_possible_match_is_only_medium_confidence(self) -> None:
        [record] = builder.from_sanctions({
            "screened": True, "status": "POSSIBLE_MATCH", "subject": "Acme", "list_size": 100,
            "matches": [{"name": "ACME TRADING", "program": "IRAN", "matched_on": "name", "confidence": 0.9}],
            "providers": [{"label": "OFAC SDN (local)", "available": True}],
        })

        assert record.confidence is Confidence.MEDIUM

    def test_an_unscreened_subject_yields_nothing(self) -> None:
        assert builder.from_sanctions({"screened": False}) == []


class TestAttribution:
    def test_every_record_names_the_agent_that_collected_it(self) -> None:
        """Attribution is what lets the console show who found what."""
        records = (
            builder.from_baseline({"business_summary": "x"}, "A")
            + builder.from_financials({"symbol": "A", "market_cap": 1e9}, "A")
            + builder.from_osint([{"title": "t", "url": "https://a.com/b"}])
            + builder.from_compliance({"obligations": [{"control": "c", "requirement": "r"}]})
        )

        collectors = {record.collector for record in records}

        assert collectors == {builder.RESEARCH, builder.FINANCIAL, builder.NEWS, builder.COMPLIANCE}
        assert all(record.id.startswith("ev_") for record in records)
