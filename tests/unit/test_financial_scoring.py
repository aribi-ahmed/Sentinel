"""Tests for the market-data dimension — the one scored with no model at all.

Two of the five dimensions never touch a language model, which is what the
report offers as the anchor making the other three defensible. An anchor is only
worth that claim if it is exercised against the inputs the market feed actually
produces, and `yfinance` produces three kinds of "missing": the string `'N/A'`,
`None`, and `float('nan')`.

The third is the dangerous one and is why this file exists. NaN is a float, so
it survives every `is not None` guard downstream, and every comparison against
it evaluates False. Before `_as_number` rejected it, a company with no market
data scored **55.3** on three invented signals — a profit margin reported as
"Negative margins signal going-concern pressure", a P/E scored 88 because NaN
fell off the end of its interpolation curve, and a free cash flow labelled
"Negative" because `nan > 0` is False. A genuinely absent feed scored 45 and
said so. The system was more alarmed by data it did not have than by data it
knew was missing.
"""

from __future__ import annotations

import math

import pytest

from sentinel.services.risk import (
    _NEUTRAL_SCORE,
    _as_number,
    _interpolate,
    score_financials,
)

NAN = float("nan")
INF = float("inf")


def metrics(**overrides) -> dict:
    payload = {
        "symbol": "TEST",
        "company_name": "Test Company",
        "market_cap": 1_000_000_000,
        "total_debt": 100_000_000,
        "pe_ratio": 15.0,
        "free_cashflow": 50_000_000,
        "profit_margins": 0.12,
    }
    payload.update(overrides)
    return payload


class TestAsNumber:
    @pytest.mark.parametrize("value", ["N/A", None, "", "not a number", True, False])
    def test_non_numbers_are_none(self, value):
        assert _as_number(value) is None

    @pytest.mark.parametrize("value", [NAN, INF, -INF, "nan", "inf", "-inf", "NaN"])
    def test_non_finite_is_none(self, value):
        """A value that cannot be compared is not a measurement."""
        assert _as_number(value) is None

    def test_real_numbers_survive(self):
        assert _as_number(12) == 12.0
        assert _as_number(-0.05) == -0.05
        assert _as_number("1,234") == 1234.0
        assert _as_number(0) == 0.0


class TestNaNProducesNoSignals:
    """The regression: NaN must read as absent, never as alarming."""

    def test_all_nan_scores_the_same_as_no_data(self):
        nan_run = score_financials(metrics(
            market_cap=NAN, total_debt=NAN, pe_ratio=NAN,
            free_cashflow=NAN, profit_margins=NAN,
        ))
        absent_run = score_financials({"symbol": "TEST"})

        assert nan_run["signals"] == []
        assert nan_run["score"] == absent_run["score"] == _NEUTRAL_SCORE

    def test_nan_margin_does_not_claim_going_concern_pressure(self):
        outcome = score_financials(metrics(profit_margins=NAN))
        assert not any(s["metric"] == "Profit margin" for s in outcome["signals"])

    def test_nan_pe_does_not_score_as_an_extreme_multiple(self):
        outcome = score_financials(metrics(pe_ratio=NAN))
        assert not any(s["metric"] == "P/E ratio" for s in outcome["signals"])

    def test_nan_cashflow_is_not_reported_as_negative(self):
        outcome = score_financials(metrics(free_cashflow=NAN))
        assert not any(s["metric"] == "Free cash flow" for s in outcome["signals"])

    def test_one_nan_does_not_discard_the_metrics_that_did_arrive(self):
        """Partial data stays usable — this is not an all-or-nothing guard."""
        outcome = score_financials(metrics(pe_ratio=NAN))
        reported = {s["metric"] for s in outcome["signals"]}
        assert "Debt to market cap" in reported
        assert "Profit margin" in reported
        assert "P/E ratio" not in reported


class TestScoringDiscriminates:
    """A scorer that returns the same number for every input explains nothing."""

    def test_leverage_raises_the_score(self):
        light = score_financials(metrics(total_debt=10_000_000))
        heavy = score_financials(metrics(total_debt=2_000_000_000))
        assert heavy["score"] > light["score"]

    def test_losses_score_worse_than_healthy_margins(self):
        loss = score_financials(metrics(profit_margins=-0.30))
        healthy = score_financials(metrics(profit_margins=0.30))
        assert loss["score"] > healthy["score"]

    def test_negative_earnings_are_scored_apart_from_an_expensive_multiple(self):
        outcome = score_financials(metrics(pe_ratio=-8.0))
        signal = next(s for s in outcome["signals"] if s["metric"] == "P/E ratio")
        assert signal["note"] == "Negative earnings"

    def test_negative_cashflow_is_reported_when_it_is_real(self):
        outcome = score_financials(metrics(free_cashflow=-1_000_000))
        signal = next(s for s in outcome["signals"] if s["metric"] == "Free cash flow")
        assert signal["value"] == "Negative"

    def test_score_stays_inside_the_band_range(self):
        for margin in (-0.9, -0.05, 0.0, 0.5):
            outcome = score_financials(metrics(profit_margins=margin))
            assert 0.0 <= outcome["score"] <= 100.0


class TestMissingDataIsStated:
    def test_absent_feed_says_so_in_the_basis(self):
        outcome = score_financials({"symbol": "TEST"})
        assert outcome["signals"] == []
        assert "no market data" in outcome["basis"].lower()

    def test_basis_counts_the_metrics_actually_used(self):
        outcome = score_financials(metrics(pe_ratio="N/A", free_cashflow="N/A"))
        assert str(len(outcome["signals"])) in outcome["basis"]

    def test_zero_market_cap_does_not_divide(self):
        outcome = score_financials(metrics(market_cap=0))
        assert not any(s["metric"] == "Debt to market cap" for s in outcome["signals"])


class TestInterpolation:
    CURVE = [(0.0, 10.0), (1.0, 50.0), (2.0, 90.0)]

    def test_below_the_first_point_clamps_low(self):
        assert _interpolate(-5.0, self.CURVE) == 10.0

    def test_above_the_last_point_clamps_high(self):
        assert _interpolate(99.0, self.CURVE) == 90.0

    def test_midpoint_interpolates_linearly(self):
        assert _interpolate(0.5, self.CURVE) == pytest.approx(30.0)

    def test_is_monotonic_across_a_rising_curve(self):
        scores = [_interpolate(x / 10, self.CURVE) for x in range(0, 25)]
        assert scores == sorted(scores)

    def test_never_returns_a_non_finite_score(self):
        for value in (-1e18, 0.0, 1e18):
            assert math.isfinite(_interpolate(value, self.CURVE))
