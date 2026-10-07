"""Tests for the memory, knowledge-graph and fraud agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.domain.evidence import Confidence, Evidence, EvidenceKind, EvidencePool
from sentinel.domain.intelligence import EntityHistory, HistoryTrend, NodeKind, PriorReview
from sentinel.services import knowledge_graph as kg
from sentinel.services.fraud import assess_fraud
from sentinel.services.memory import normalise


def at(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def pool_of(*kinds: str) -> EvidencePool:
    pool = EvidencePool()
    for index, kind in enumerate(kinds):
        pool.add(Evidence(
            summary="record",
            kind=EvidenceKind(kind),
            source="source",
            collector="agent",
            confidence=Confidence.HIGH,
            id=f"ev_{index}",
        ))
    return pool


# --------------------------------------------------------------------------- #
#  Memory                                                                      #
# --------------------------------------------------------------------------- #

class TestNormalisation:
    @pytest.mark.parametrize("left,right", [
        ("Alphabet Inc.", "Alphabet Incorporated"),
        ("Wolfspeed, Inc.", "Wolfspeed Inc"),
        ("Acme Trading Limited", "Acme Trading Ltd"),
    ])
    def test_spelling_variants_match(self, left: str, right: str) -> None:
        assert normalise(left) == normalise(right)

    def test_different_entities_do_not_match(self) -> None:
        """Fuzzy matching here would corrupt the comparison it exists to give."""
        assert normalise("Alphabet Inc.") != normalise("Alphabet International DMCC")


class TestEntityHistory:
    def test_no_record_is_a_first_review(self) -> None:
        history = EntityHistory(subject="Acme")

        assert history.is_first_review
        assert history.trend is HistoryTrend.FIRST_REVIEW
        assert "No prior review" in history.compare("LOW")

    def test_a_rising_band_reads_as_deteriorating(self) -> None:
        history = EntityHistory(subject="Acme", reviews=(
            PriorReview("i3", "ELEVATED", at(1)),
            PriorReview("i2", "MODERATE", at(30)),
            PriorReview("i1", "LOW", at(60)),
        ))

        assert history.trend is HistoryTrend.DETERIORATING

    def test_a_falling_band_reads_as_improving(self) -> None:
        history = EntityHistory(subject="Acme", reviews=(
            PriorReview("i2", "LOW", at(1)),
            PriorReview("i1", "SEVERE", at(60)),
        ))

        assert history.trend is HistoryTrend.IMPROVING

    def test_an_unchanged_band_is_stable(self) -> None:
        history = EntityHistory(subject="Acme", reviews=(
            PriorReview("i2", "MODERATE", at(1)),
            PriorReview("i1", "MODERATE", at(60)),
        ))

        assert history.trend is HistoryTrend.STABLE

    def test_the_comparison_names_the_movement(self) -> None:
        history = EntityHistory(subject="Acme", reviews=(PriorReview("i1", "LOW", at(10)),))
        assert "up from LOW" in history.compare("ELEVATED")

    def test_declined_verdicts_are_surfaced(self) -> None:
        """An analyst having rejected this entity before is material context."""
        history = EntityHistory(subject="Acme", reviews=(
            PriorReview("i1", "MODERATE", at(10), approved=False),
        ))

        assert history.rejection_count == 1
        assert "declined by an analyst" in history.compare("MODERATE")

    def test_an_unknown_band_does_not_break_the_trend(self) -> None:
        history = EntityHistory(subject="Acme", reviews=(PriorReview("i1", "", at(10)),))
        assert history.trend is HistoryTrend.STABLE


# --------------------------------------------------------------------------- #
#  Knowledge graph                                                             #
# --------------------------------------------------------------------------- #

class TestKnowledgeGraph:
    def test_the_subject_is_always_the_root(self) -> None:
        graph = kg.build_graph(subject="Acme Trading", ticker="")

        assert graph.nodes[0].kind is NodeKind.SUBJECT
        assert graph.nodes[0].label == "Acme Trading"

    def test_a_registrant_becomes_a_connected_entity(self) -> None:
        graph = kg.build_graph(
            subject="Wolfspeed",
            ticker="WOLF",
            edgar={
                "matched": True, "company": "WOLFSPEED, INC.", "cik": "0000895419",
                "state_of_incorporation": "DE", "sic_description": "Semiconductors",
                "exchanges": ["NYSE"],
            },
            evidence=pool_of("filing"),
        )

        kinds = {n.kind for n in graph.nodes}
        assert NodeKind.REGISTRANT in kinds
        assert NodeKind.JURISDICTION in kinds
        assert NodeKind.SECTOR in kinds
        assert len(graph.edges) >= 4

    def test_an_unmatched_registrant_adds_nothing(self) -> None:
        graph = kg.build_graph(subject="Acme", edgar={"matched": False})
        assert len(graph.nodes) == 1

    def test_a_watchlist_match_is_flagged(self) -> None:
        """The point of the graph: exposure one hop from the subject."""
        graph = kg.build_graph(
            subject="Bank Melli",
            sanctions={"matches": [
                {"name": "BANK MELLI IRAN", "confidence": 0.98, "programs": ["IRAN", "SDGT"]},
            ]},
            evidence=pool_of("watchlist"),
        )

        flagged = graph.flagged_nodes
        assert flagged
        assert any(n.kind is NodeKind.PROGRAMME for n in flagged)
        assert "watchlist association" in graph.summary()

    def test_organisations_in_reporting_are_linked(self) -> None:
        graph = kg.build_graph(
            subject="Acme",
            news=[{"content": "Acme signed a deal with Contoso Holdings Ltd this quarter."}],
            evidence=pool_of("open_source"),
        )

        assert any("Contoso" in n.label for n in graph.nodes)

    def test_the_subject_is_not_linked_to_itself(self) -> None:
        graph = kg.build_graph(
            subject="Wolfspeed",
            news=[{"content": "Wolfspeed Inc reported results."}],
        )

        assert all(e.source != e.target for e in graph.edges)
        assert len(graph.nodes) == 1, "the subject under another spelling is not a relationship"

    def test_edges_never_dangle(self) -> None:
        graph = kg.build_graph(subject="Acme")
        graph.connect("subject", "nonexistent", kg.EdgeKind.MENTIONED_WITH)

        assert not graph.edges

    def test_neighbours_are_resolvable(self) -> None:
        graph = kg.build_graph(
            subject="Wolfspeed",
            edgar={"matched": True, "company": "WOLFSPEED, INC.", "cik": "1", "exchanges": ["NYSE"]},
        )

        assert graph.neighbours("subject")


# --------------------------------------------------------------------------- #
#  Fraud signals                                                               #
# --------------------------------------------------------------------------- #

class TestFraudSignals:
    def test_a_clean_entity_scores_zero(self) -> None:
        assessment = assess_fraud(edgar={"matched": True, "flags": []}, financials={})

        assert assessment.score == 0.0
        assert assessment.band == "MINIMAL"
        assert "No fraud indicators" in assessment.summary()

    def test_a_restatement_is_the_strongest_single_signal(self) -> None:
        assessment = assess_fraud(
            edgar={"flags": [{"code": "4.02", "form": "8-K", "date": "2025-06-01"}]},
            evidence=pool_of("filing"),
        )

        assert assessment.signals
        assert assessment.signals[0].label.startswith("Financial statements declared")

    def test_signals_are_categorised(self) -> None:
        assessment = assess_fraud(
            edgar={"flags": [
                {"code": "4.01", "form": "8-K", "date": "2025-01-01"},
                {"form": "NT 10-K", "code": "", "date": "2025-03-01"},
            ]},
            financials={"total_debt": 900, "market_cap": 400},
        )

        assert len(assessment.categories) >= 2

    def test_convergence_across_categories_raises_the_score(self) -> None:
        """The whole reason this agent exists."""
        one_category = assess_fraud(
            edgar={"flags": [{"form": "NT 10-K", "code": "", "date": "2025-03-01"}]},
        )
        three_categories = assess_fraud(
            edgar={"flags": [
                {"form": "NT 10-K", "code": "", "date": "2025-03-01"},
                {"code": "4.01", "form": "8-K", "date": "2025-02-01"},
            ]},
            financials={"total_debt": 900, "market_cap": 400},
        )

        assert three_categories.score > one_category.score
        assert len(three_categories.categories) > len(one_category.categories)

    def test_an_audit_breakdown_is_named(self) -> None:
        assessment = assess_fraud(edgar={"flags": [
            {"code": "4.01", "form": "8-K", "date": "2025-01-01"},
            {"code": "4.02", "form": "8-K", "date": "2025-02-01"},
        ]})

        assert any(p.id == "fp_audit_breakdown" for p in assessment.patterns)

    def test_pressure_with_a_reporting_failure_is_named(self) -> None:
        assessment = assess_fraud(
            edgar={"flags": [{"form": "NT 10-K", "code": "", "date": "2025-03-01"}]},
            financials={"total_debt": 900, "market_cap": 400, "free_cashflow": -50_000},
        )

        assert any(p.id == "fp_pressure_and_failure" for p in assessment.patterns)

    def test_reporting_language_is_detected(self) -> None:
        assessment = assess_fraud(
            news=[{"content": "The SEC investigation into the restatement continues."}],
            evidence=pool_of("open_source"),
        )

        labels = {s.label for s in assessment.signals}
        assert "Regulatory investigation referenced" in labels
        assert "Restatement referenced" in labels

    def test_the_corporate_baseline_is_scanned_too(self) -> None:
        """Enforcement history often appears there and not in the news sweep."""
        assessment = assess_fraud(
            research=[{"profile": "The company paid a civil penalty following an SEC probe."}],
            evidence=pool_of("baseline"),
        )

        assert assessment.signals

    def test_several_wordings_collapse_to_one_signal(self) -> None:
        """Otherwise a single finding restated three ways triples the score."""
        assessment = assess_fraud(
            news="securities fraud, misleading statements and misrepresentation alleged",
        )

        assert len([s for s in assessment.signals if s.label == "Securities fraud alleged"]) == 1

    def test_open_source_signals_are_marked_as_unadjudicated(self) -> None:
        assessment = assess_fraud(news="reports of accounting fraud surfaced")
        assert all("not an adjudicated finding" in s.detail for s in assessment.signals)

    def test_a_departure_cluster_needs_repetition(self) -> None:
        few = assess_fraud(edgar={"counts": {"5.02": 2}})
        many = assess_fraud(edgar={"counts": {"5.02": 7}})

        assert not few.signals
        assert many.signals

    def test_the_score_is_capped(self) -> None:
        assessment = assess_fraud(
            edgar={"flags": [
                {"code": code, "form": "8-K", "date": "2025-01-01"}
                for code in ("4.02", "4.01", "1.03", "3.01", "2.06", "2.04", "5.02", "1.02")
            ], "counts": {"5.02": 9, "1.02": 6}},
            financials={"total_debt": 900, "market_cap": 100, "profit_margins": -0.9,
                        "free_cashflow": -1},
            news="accounting fraud sec investigation going concern class action",
        )

        assert assessment.score <= 100.0
        assert assessment.band == "SEVERE"

    def test_duplicate_signals_are_collapsed(self) -> None:
        assessment = assess_fraud(edgar={"flags": [
            {"code": "4.02", "form": "8-K", "date": "2025-01-01"},
            {"code": "4.02", "form": "8-K", "date": "2025-05-01"},
        ]})

        assert len([s for s in assessment.signals if s.id == "fs_4_02"]) == 1

    def test_non_numeric_market_data_is_ignored(self) -> None:
        assessment = assess_fraud(financials={"total_debt": "N/A", "market_cap": "N/A"})
        assert assessment.score == 0.0


class TestSignalProvenance:
    """A press allegation and a filed disclosure are not the same evidence."""

    def test_filing_signals_are_marked_disclosed(self) -> None:
        assessment = assess_fraud(edgar={"flags": [
            {"code": "4.02", "form": "8-K", "date": "2025-06-01"},
        ]})

        assert all(s.disclosed for s in assessment.signals)
        assert assessment.disclosed_score > 0

    def test_reporting_signals_are_not_disclosed(self) -> None:
        assessment = assess_fraud(news="securities class action and penalty reported")

        assert not any(s.disclosed for s in assessment.signals)
        assert assessment.disclosed_score == 0.0

    def test_the_summary_states_the_provenance(self) -> None:
        """A reader must be able to tell an allegation from a filed fact."""
        reported = assess_fraud(news="class action lawsuit")
        assert "none from regulatory disclosures" in reported.summary()

        disclosed = assess_fraud(edgar={"flags": [{"code": "4.02", "form": "8-K"}]})
        assert "from regulatory disclosures" in disclosed.summary()

    def test_disclosed_score_never_exceeds_the_headline(self) -> None:
        assessment = assess_fraud(
            edgar={"flags": [{"code": "4.02", "form": "8-K"}]},
            news="class action lawsuit penalty",
        )

        assert assessment.disclosed_score <= assessment.score


class TestSignalPrecision:
    """The same entity scored 53 on one run and 7 on the next, because bare
    words like "lawsuit" fire on whichever three articles a search returned."""

    @pytest.mark.parametrize("noise", [
        "a lawsuit was filed against the company",
        "regulatory violation reported by the agency",
        "the company was fined last year",
        "ongoing litigation in several states",
        "an internal complaint was raised",
    ])
    def test_generic_language_does_not_fire(self, noise: str) -> None:
        assert assess_fraud(news=noise).score == 0.0

    @pytest.mark.parametrize("signal", [
        "a securities class action was filed",
        "a material weakness in internal control over financial reporting",
        "the auditor resigned in March",
        "an SEC investigation was opened",
        "substantial doubt about the company's ability to continue",
        "the whistleblower alleged misconduct",
    ])
    def test_specific_language_still_fires(self, signal: str) -> None:
        assert assess_fraud(news=signal).signals

    def test_the_evidence_pool_is_scanned(self) -> None:
        """Pool records are normalised, so they do not move between runs."""
        pool = EvidencePool()
        pool.add(Evidence(
            summary="Auditor resigned citing disagreement",
            kind=EvidenceKind.OPEN_SOURCE,
            source="reuters.com",
            collector="news_analyst",
            confidence=Confidence.MEDIUM,
            id="ev_1",
        ))

        assert assess_fraud(evidence=pool).signals

    def test_a_distressed_issuer_still_scores_severe(self) -> None:
        """Precision must not be bought by missing the real cases."""
        assessment = assess_fraud(
            edgar={"flags": [
                {"code": "1.03", "form": "8-K", "date": "2025-06-01"},
                {"form": "25-NSE", "code": "", "date": "2025-07-01"},
                {"code": "2.04", "form": "8-K", "date": "2025-05-01"},
            ]},
            financials={"total_debt": 900, "market_cap": 400, "free_cashflow": -1},
        )

        assert assessment.band == "SEVERE"
        assert assessment.disclosed_score > 0
