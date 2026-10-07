"""Harness tests.

The harness is itself code, so it gets tested like code — with a stub runner
returning fixed payloads. That matters: a scorer that silently passes everything
would make the whole evaluation worthless, and only a test with a known-bad
input can catch that.

The live golden run is marked `integration`: it makes real model calls and takes
minutes, so it stays out of the fast suite.
"""

from __future__ import annotations

import pytest

from sentinel.evaluation.cases import GOLDEN_CASES, GoldenCase, case_by_id, cases_for
from sentinel.evaluation.harness import EvaluationReport, compare_to_baseline, evaluate
from sentinel.evaluation.scorers import score_case

SPECIALISTS = ("research_analyst", "financial_analyst", "news_analyst", "compliance_analyst")


def good_result(**overrides) -> dict:
    """A payload that should satisfy every scorer."""
    evidence = [
        {"id": "ev_1", "kind": "filing", "source": "SEC EDGAR", "confidence": "high"},
        {"id": "ev_2", "kind": "market_data", "source": "yfinance", "confidence": "high"},
        {"id": "ev_3", "kind": "watchlist", "source": "OFAC SDN", "confidence": "high"},
        {"id": "ev_4", "kind": "policy", "source": "DOJ ECCP", "confidence": "medium"},
        {"id": "ev_5", "kind": "open_source", "source": "reuters.com", "confidence": "medium"},
        {"id": "ev_6", "kind": "baseline", "source": "Research agent", "confidence": "low"},
        {"id": "ev_7", "kind": "filing", "source": "SEC EDGAR", "confidence": "high"},
        {"id": "ev_8", "kind": "market_data", "source": "yfinance", "confidence": "high"},
    ]
    weights = [
        ("sanctions", 0.30), ("legal_regulatory", 0.25), ("financial", 0.20),
        ("reputational", 0.15), ("governance", 0.10),
    ]
    payload = {
        "id": "inv_test",
        "plan": {
            "strategy": "full",
            "engaged": list(SPECIALISTS),
            "skipped": [],
            "decisions": [
                {"agent": agent, "engaged": True, "reason": "engaged"} for agent in SPECIALISTS
            ],
        },
        "sanctions_data": {"status": "CLEAR"},
        "edgar_data": {"matched": True},
        "critic_review": {
            "verdict": "endorsed",
            "challenges": [],
            "confidence_penalty": 0.0,
            "revision": 0,
        },
        "risk_assessment": {
            "score": 25.0,
            "band": "LOW",
            "evidence": evidence,
            "dimensions": [
                {
                    "id": name, "weight": weight, "score": 25.0, "assessed": True,
                    "grounded": True, "evidence_ids": ["ev_1"],
                }
                for name, weight in weights
            ],
        },
    }
    payload.update(overrides)
    return payload


ALPHABET = case_by_id("alphabet-low")


class TestGoldenDataset:
    def test_case_ids_are_unique(self) -> None:
        ids = [case.id for case in GOLDEN_CASES]
        assert len(ids) == len(set(ids))

    def test_every_case_explains_why_it_exists(self) -> None:
        """A case with no rationale becomes noise the moment it fails."""
        assert all(len(case.rationale.strip()) > 40 for case in GOLDEN_CASES)

    def test_score_ranges_are_well_formed(self) -> None:
        for case in GOLDEN_CASES:
            low, high = case.score_range
            assert 0 <= low < high <= 100, case.id

    def test_the_dataset_spans_the_scale(self) -> None:
        """A dataset of only-clean cases could not detect under-flagging."""
        lows = [case for case in GOLDEN_CASES if case.score_range[1] <= 50]
        highs = [case for case in GOLDEN_CASES if case.score_range[0] >= 55]
        assert lows and highs

    def test_selection_by_id(self) -> None:
        assert len(cases_for(("alphabet-low",))) == 1
        assert cases_for(("nope",)) == ()


class TestScorers:
    def test_a_good_result_passes_every_check(self) -> None:
        score = score_case(good_result(), ALPHABET)
        assert score.ok, [f.to_dict() for f in score.failures]

    def test_a_dangling_citation_is_caught(self) -> None:
        """The regression guard for model-declared citations."""
        result = good_result()
        result["risk_assessment"]["dimensions"][0]["evidence_ids"] = ["ev_does_not_exist"]

        assert "groundedness.citations_resolve" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_an_ungrounded_factor_is_caught(self) -> None:
        result = good_result()
        result["risk_assessment"]["dimensions"][1]["grounded"] = False

        assert "groundedness.all_assessed_factors_grounded" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_press_filed_at_high_confidence_is_caught(self) -> None:
        """Rumour must never be weighed like a regulatory disclosure."""
        result = good_result()
        result["risk_assessment"]["evidence"][4]["confidence"] = "high"

        assert "groundedness.confidence_by_provenance" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_score_outside_the_expected_range_is_caught(self) -> None:
        result = good_result()
        result["risk_assessment"]["score"] = 90.0
        result["risk_assessment"]["band"] = "SEVERE"

        assert "calibration.score_in_expected_range" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_band_inconsistent_with_its_score_is_caught(self) -> None:
        result = good_result()
        result["risk_assessment"]["band"] = "SEVERE"  # the score is 25

        assert "schema.band_matches_score" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_weights_that_do_not_sum_to_one_are_caught(self) -> None:
        result = good_result()
        result["risk_assessment"]["dimensions"][0]["weight"] = 0.9

        assert "schema.weights_sum_to_one" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_wrong_sanctions_status_is_caught(self) -> None:
        result = good_result(sanctions_data={"status": "HIT"})

        assert "tools.sanctions_status" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_routing_decision_without_a_reason_is_caught(self) -> None:
        result = good_result()
        result["plan"]["decisions"][0]["reason"] = ""

        assert "routing.every_decision_has_a_reason" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_missing_evidence_kinds_are_caught(self) -> None:
        result = good_result()
        result["risk_assessment"]["evidence"] = [
            item for item in result["risk_assessment"]["evidence"] if item["kind"] != "filing"
        ]

        assert "completeness.expected_evidence_kinds" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_missing_critic_review_is_caught(self) -> None:
        """An investigation that skipped the review is not a complete run."""
        result = good_result()
        result.pop("critic_review")

        assert "critic.review_present" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_verdict_inconsistent_with_its_challenges_is_caught(self) -> None:
        """A critic that files a blocking objection and then endorses is broken."""
        result = good_result()
        result["critic_review"] = {
            "verdict": "endorsed",
            "challenges": [{"severity": "blocking", "detail": "citation does not resolve"}],
            "confidence_penalty": 0.0,
            "revision": 0,
        }

        assert "critic.verdict_follows_challenges" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_challenge_with_no_detail_is_caught(self) -> None:
        result = good_result()
        result["critic_review"] = {
            "verdict": "qualified",
            "challenges": [{"severity": "material", "detail": "  "}],
            "confidence_penalty": 0.05,
            "revision": 0,
        }

        assert "critic.every_challenge_is_actionable" in {
            f.name for f in score_case(result, ALPHABET).failures
        }

    def test_a_malformed_payload_is_not_scored_as_acceptable(self) -> None:
        assert not score_case({"risk_assessment": "not a dict"}, ALPHABET).ok


class TestHarness:
    def test_runs_every_case_with_a_stub_runner(self) -> None:
        report = evaluate(GOLDEN_CASES[:3], runner=lambda case: good_result(), verbose=False)
        assert report.cases == 3

    def test_a_runner_error_becomes_a_failed_case_not_a_crash(self) -> None:
        def explode(case: GoldenCase) -> dict:
            raise RuntimeError("provider unavailable")

        report = evaluate(GOLDEN_CASES[:2], runner=explode, verbose=False)

        assert report.passed_cases == 0
        assert all("provider unavailable" in score.error for score in report.scores)

    def test_report_serialises(self) -> None:
        report = evaluate(GOLDEN_CASES[:2], runner=lambda case: good_result(), verbose=False)
        assert set(report.to_dict()) >= {"cases", "checks_passed", "rate", "by_case"}

    def test_render_names_the_failing_check(self) -> None:
        report = evaluate(
            [ALPHABET],
            runner=lambda case: good_result(sanctions_data={"status": "HIT"}),
            verbose=False,
        )
        assert "tools.sanctions_status" in report.render()


class TestBaselineComparison:
    def test_no_baseline_is_not_a_regression(self) -> None:
        report = evaluate([ALPHABET], runner=lambda case: good_result(), verbose=False)
        assert compare_to_baseline(report, baseline=None)["has_baseline"] is False

    def test_a_drop_beyond_tolerance_is_reported(self) -> None:
        """Note the tolerance is *relative*: as the suite grows, one failing
        check is a smaller fraction of the total. That is why two are broken
        here rather than one. It does not weaken the harness - `__main__`
        fails the run on any failing case regardless of the baseline - but it
        does mean the baseline comparison tracks broad quality rather than
        single-check regressions."""
        def degraded(case):
            result = good_result(sanctions_data={"status": "HIT"})
            result["risk_assessment"]["dimensions"][0]["evidence_ids"] = ["ev_missing"]
            return result

        report = evaluate([ALPHABET], runner=degraded, verbose=False)
        baseline = {"started_at": "t0", "rate": 1.0, "by_case": {ALPHABET.id: {"rate": 1.0}}}

        comparison = compare_to_baseline(report, baseline)

        assert comparison["regressions"]
        assert comparison["regressions"][0]["case_id"] == ALPHABET.id

    def test_an_improvement_never_fails_a_run(self) -> None:
        """Otherwise nobody would ever raise the bar."""
        report = evaluate([ALPHABET], runner=lambda case: good_result(), verbose=False)
        baseline = {"started_at": "t0", "rate": 0.5, "by_case": {ALPHABET.id: {"rate": 0.5}}}

        comparison = compare_to_baseline(report, baseline)

        assert not comparison["regressions"]
        assert comparison["improvements"]

    def test_small_variance_is_not_a_regression(self) -> None:
        report = evaluate([ALPHABET], runner=lambda case: good_result(), verbose=False)
        baseline = {"started_at": "t0", "rate": 1.0, "by_case": {ALPHABET.id: {"rate": 1.02}}}

        assert not compare_to_baseline(report, baseline)["regressions"]


@pytest.mark.integration
def test_golden_dataset_against_the_live_system() -> None:
    """The real thing: every case run end to end. Minutes, and real model calls."""
    report = evaluate(GOLDEN_CASES, verbose=False)

    assert report.passed_cases == report.cases, report.render()
