"""Fraud-signal detection and convergence scoring.

Individual red flags are collected elsewhere and each sets a floor under the
regulatory dimension. This module scores them *jointly*: an auditor change, a
late filing and an officer exodus inside the same window describe a different
situation than any one of them alone.

The model follows the standard fraud triangle — pressure, opportunity, and the
disclosure event where a misstatement surfaces. Coverage across categories is
what drives the uplift, because a company under financial pressure with intact
controls and clean reporting is distressed, not misreporting.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from sentinel.domain.evidence import EvidencePool
from sentinel.domain.intelligence import (
    FraudAssessment,
    FraudPattern,
    FraudSignal,
    SignalCategory,
)

# 8-K item codes that carry a fraud-relevant signal, mapped to the condition
# they evidence and the weight each contributes before convergence.
ITEM_SIGNALS: Dict[str, Tuple[str, SignalCategory, float]] = {
    "4.02": ("Financial statements declared unreliable", SignalCategory.DISCLOSURE, 34.0),
    "4.01": ("Certifying accountant replaced", SignalCategory.OPPORTUNITY, 20.0),
    "1.03": ("Bankruptcy or receivership", SignalCategory.PRESSURE, 26.0),
    "3.01": ("Listing-rule failure", SignalCategory.DISCLOSURE, 18.0),
    "2.06": ("Material asset impairment", SignalCategory.PRESSURE, 12.0),
    "2.04": ("Debt obligation accelerated", SignalCategory.PRESSURE, 14.0),
    "5.02": ("Director or officer departures", SignalCategory.OPPORTUNITY, 12.0),
    "1.02": ("Material agreements terminated", SignalCategory.PRESSURE, 8.0),
}

FORM_SIGNALS: Dict[str, Tuple[str, SignalCategory, float]] = {
    "NT 10-K": ("Annual report filed late", SignalCategory.DISCLOSURE, 22.0),
    "NT 10-Q": ("Quarterly report filed late", SignalCategory.DISCLOSURE, 14.0),
    "25-NSE": ("Exchange delisting notice", SignalCategory.DISCLOSURE, 20.0),
    "15-12B": ("Securities deregistered", SignalCategory.DISCLOSURE, 12.0),
}

# Language in collected reporting that evidences one of the three conditions.
# Phrasings are grouped so several wordings of the same finding collapse into a
# single signal instead of being counted repeatedly.
ADVERSE_TERMS = (
    ("Accounting irregularity alleged",
     ("accounting fraud", "improper accounting", "accounting irregularit",
      "revenue recognition issue", "channel stuffing", "falsified account"),
     SignalCategory.DISCLOSURE, 20.0),
    ("Restatement referenced",
     ("restatement", "restate its financial", "restated its financial",
      "non-reliance", "financial statements should no longer be relied"),
     SignalCategory.DISCLOSURE, 16.0),
    ("Securities fraud alleged",
     ("securities fraud", "misled investors", "misleading statements to investors",
      "material misrepresentation", "false and misleading"),
     SignalCategory.DISCLOSURE, 16.0),
    ("Regulatory investigation referenced",
     ("sec investigation", "sec probe", "doj investigation", "criminal probe",
      "grand jury", "federal investigation into", "formal order of investigation",
      "subpoena"),
     SignalCategory.DISCLOSURE, 16.0),
    ("Enforcement penalty referenced",
     ("civil penalty", "consent decree", "deferred prosecution", "enforcement action",
      "settlement with the sec", "settled with the sec", "monetary penalty"),
     SignalCategory.DISCLOSURE, 12.0),
    ("Whistleblower complaint referenced",
     ("whistleblower", "retaliation claim"),
     SignalCategory.OPPORTUNITY, 14.0),
    ("Internal control weakness referenced",
     ("material weakness", "internal control over financial reporting",
      "control deficiency", "auditor resigned", "auditor dismissed",
      "audit committee investigation"),
     SignalCategory.OPPORTUNITY, 16.0),
    ("Executive turnover referenced",
     ("cfo resigned", "cfo departed", "chief financial officer resigned",
      "chief financial officer departed", "stepped down amid", "ousted as chief"),
     SignalCategory.OPPORTUNITY, 12.0),
    ("Governance dispute referenced",
     ("shareholder derivative", "derivative suit", "proxy fight",
      "board of directors dispute", "breach of fiduciary duty"),
     SignalCategory.OPPORTUNITY, 9.0),
    ("Securities class action referenced",
     ("securities class action", "class action lawsuit", "securities litigation",
      "investor class action"),
     SignalCategory.PRESSURE, 9.0),
    ("Going-concern doubt referenced",
     ("going concern", "substantial doubt about", "liquidity crisis",
      "covenant breach", "covenant waiver", "debt restructuring"),
     SignalCategory.PRESSURE, 14.0),
    ("Short-seller allegations referenced",
     ("short seller report", "short-seller report", "activist short"),
     SignalCategory.PRESSURE, 6.0),
)

MAX_SCORE = 100.0

# Uplift applied when signals span multiple categories. Two conditions present
# is a meaningfully different picture from one; all three more so again.
COVERAGE_UPLIFT = {1: 0.0, 2: 10.0, 3: 22.0}


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return str(value or "")


def _signals_from_filings(edgar: Dict[str, Any], pool: EvidencePool) -> List[FraudSignal]:
    signals: List[FraudSignal] = []
    filing_ids = tuple(item.id for item in pool if item.kind.value == "filing")

    for flag in edgar.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        code = str(flag.get("code") or "")
        form = str(flag.get("form") or "")

        entry = ITEM_SIGNALS.get(code) or FORM_SIGNALS.get(form)
        if entry is None:
            continue

        label, category, weight = entry
        signals.append(FraudSignal(
            id=f"fs_{code or form}".replace(" ", "_").replace(".", "_").lower(),
            label=label,
            category=category,
            weight=weight,
            detail=f"{form or '8-K'} disclosed {flag.get('date') or 'in the last 24 months'}.",
            evidence_ids=filing_ids[:3],
            observed_at=str(flag.get("date") or ""),
            disclosed=True,
        ))

    counts = edgar.get("counts") or {}
    for code, entry in (("5.02", ITEM_SIGNALS["5.02"]), ("1.02", ITEM_SIGNALS["1.02"])):
        occurrences = int(counts.get(code) or 0)
        if occurrences < 4:
            continue
        label, category, weight = entry
        signal_id = f"fs_cluster_{code.replace('.', '_')}"
        if any(s.id == signal_id for s in signals):
            continue
        signals.append(FraudSignal(
            id=signal_id,
            label=f"{label} ({occurrences} in 24 months)",
            category=category,
            weight=weight,
            detail=f"{occurrences} separate item {code} disclosures inside the review window.",
            evidence_ids=filing_ids[:2],
            disclosed=True,
        ))

    return signals


def _signals_from_financials(financials: Dict[str, Any], pool: EvidencePool) -> List[FraudSignal]:
    signals: List[FraudSignal] = []
    market_ids = tuple(item.id for item in pool if item.kind.value == "market_data")

    def numeric(key: str) -> Optional[float]:
        value = financials.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    debt, cap = numeric("total_debt"), numeric("market_cap")
    if debt and cap and debt > cap:
        signals.append(FraudSignal(
            id="fs_leverage",
            label="Debt exceeds market capitalisation",
            category=SignalCategory.PRESSURE,
            weight=14.0,
            detail=f"Total debt {debt:,.0f} against a market capitalisation of {cap:,.0f}.",
            evidence_ids=market_ids[:2],
        ))

    margin = numeric("profit_margins")
    if margin is not None and margin < -0.25:
        signals.append(FraudSignal(
            id="fs_margin",
            label="Deeply negative profit margin",
            category=SignalCategory.PRESSURE,
            weight=10.0,
            detail=f"Profit margin of {margin:.0%} sustains pressure on reported results.",
            evidence_ids=market_ids[:2],
        ))

    cashflow = numeric("free_cashflow")
    if cashflow is not None and cashflow < 0:
        signals.append(FraudSignal(
            id="fs_cashflow",
            label="Negative free cash flow",
            category=SignalCategory.PRESSURE,
            weight=8.0,
            detail=f"Free cash flow of {cashflow:,.0f}.",
            evidence_ids=market_ids[:2],
        ))

    return signals


def _signals_from_reporting(news: Any, research: Any, pool: EvidencePool) -> List[FraudSignal]:
    """Scans everything the specialists collected for the three conditions.

    The evidence pool is included alongside the raw payloads deliberately. Its
    records are normalised summaries rather than whatever three articles a web
    search returned that minute, which is what keeps the score from moving
    between runs on the same entity.
    """
    collected = " ".join(
        f"{item.summary} {item.detail}" for item in pool
    )
    text = f"{_as_text(news)} {_as_text(research)} {collected}".lower()
    if not text.strip():
        return []

    open_ids = tuple(item.id for item in pool if item.kind.value == "open_source")
    baseline_ids = tuple(item.id for item in pool if item.kind.value == "baseline")
    cited = (open_ids or baseline_ids)[:2]

    signals: List[FraudSignal] = []
    for label, terms, category, weight in ADVERSE_TERMS:
        matched = next((term for term in terms if term in text), None)
        if matched is None:
            continue

        signals.append(FraudSignal(
            id="fs_news_" + label.lower().replace(" ", "_")[:40],
            label=label,
            category=category,
            weight=weight,
            # Reporting corroborates, it does not adjudicate. Every weight here
            # sits below the equivalent regulatory disclosure above.
            detail=f"Matched on '{matched}' in collected reporting; not an adjudicated finding.",
            evidence_ids=cited,
        ))

    return signals


def _patterns(signals: Sequence[FraudSignal]) -> List[FraudPattern]:
    """Named combinations that carry more weight than their parts."""
    by_id = {s.id: s for s in signals}
    present = set(by_id)
    found: List[FraudPattern] = []

    def has(*ids: str) -> bool:
        return all(i in present for i in ids)

    if has("fs_4_01", "fs_nt_10-k") or has("fs_4_01", "fs_4_02"):
        found.append(FraudPattern(
            id="fp_audit_breakdown",
            label="Audit breakdown",
            detail=(
                "The certifying accountant changed alongside a reporting failure. "
                "Auditor turnover concurrent with late or restated filings is the "
                "most frequently cited precursor to a restatement."
            ),
            signal_ids=tuple(i for i in ("fs_4_01", "fs_nt_10-k", "fs_4_02") if i in present),
            uplift=14.0,
        ))

    if has("fs_cluster_5_02") and any(s.category is SignalCategory.DISCLOSURE for s in signals):
        found.append(FraudPattern(
            id="fp_governance_exodus",
            label="Governance exodus with reporting failure",
            detail=(
                "Senior departures cluster in the same window as a disclosure failure, "
                "removing the oversight that would ordinarily catch it."
            ),
            signal_ids=("fs_cluster_5_02",),
            uplift=12.0,
        ))

    pressure = [s for s in signals if s.category is SignalCategory.PRESSURE]
    disclosure = [s for s in signals if s.category is SignalCategory.DISCLOSURE]
    if len(pressure) >= 2 and disclosure:
        found.append(FraudPattern(
            id="fp_pressure_and_failure",
            label="Financial pressure with reporting failure",
            detail=(
                f"{len(pressure)} distress indicators coincide with {len(disclosure)} "
                "reporting failure(s): the incentive to misstate and the event where "
                "misstatement surfaces are both present."
            ),
            signal_ids=tuple(s.id for s in (*pressure[:2], *disclosure[:1])),
            uplift=10.0,
        ))

    if has("fs_4_02", "fs_news_accounting_fraud") or has("fs_4_02", "fs_news_sec_investigation"):
        found.append(FraudPattern(
            id="fp_corroborated_restatement",
            label="Restatement corroborated externally",
            detail=(
                "A declared unreliability of financial statements is independently "
                "referenced in open-source reporting."
            ),
            signal_ids=tuple(
                i for i in ("fs_4_02", "fs_news_accounting_fraud", "fs_news_sec_investigation")
                if i in present
            ),
            uplift=12.0,
        ))

    return found


def assess_fraud(
    *,
    edgar: Optional[Dict[str, Any]] = None,
    financials: Optional[Dict[str, Any]] = None,
    news: Any = None,
    evidence: Optional[EvidencePool] = None,
    research: Any = None,
) -> FraudAssessment:
    """Collects fraud indicators and scores their convergence."""
    pool = evidence if evidence is not None else EvidencePool()

    signals: List[FraudSignal] = []
    signals += _signals_from_filings(edgar or {}, pool)
    signals += _signals_from_financials(financials or {}, pool)
    signals += _signals_from_reporting(news, research, pool)

    # De-duplicate while preserving order.
    unique: List[FraudSignal] = []
    seen: set = set()
    for signal in signals:
        if signal.id in seen:
            continue
        seen.add(signal.id)
        unique.append(signal)

    if not unique:
        return FraudAssessment(score=0.0)

    patterns = _patterns(unique)

    # The strongest signal anchors the score; the rest contribute at a discount
    # so a long tail of minor flags cannot outweigh one severe disclosure.
    ordered = sorted(unique, key=lambda s: s.weight, reverse=True)
    base = ordered[0].weight + sum(s.weight * 0.45 for s in ordered[1:])

    categories = {s.category for s in unique}
    base += COVERAGE_UPLIFT.get(len(categories), 0.0)
    base += sum(p.uplift for p in patterns)

    return FraudAssessment(
        score=min(MAX_SCORE, round(base, 1)),
        signals=tuple(unique),
        patterns=tuple(patterns),
    )
