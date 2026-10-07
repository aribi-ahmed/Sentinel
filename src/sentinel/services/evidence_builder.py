"""Turns raw tool output into citable `Evidence`.

Each specialist gathers facts in whatever shape its tool returns them — a
yfinance dict, an OFAC match list, a Tavily result set. This module is the one
place that normalises those into the domain's `Evidence` type, so that:

* every fact carries a source, a collector and a confidence, and
* every fact gets a stable id that a `RiskFactor` can cite.

Confidence here means *how much we trust the fact*, not how bad it is. A filing
the entity made itself is HIGH; a press article is MEDIUM at best. Getting that
distinction right is what stops the scorer treating a rumour like a disclosure.

Depends on the domain only — no tool SDKs, so it stays unit-testable.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sentinel.domain import Confidence, Evidence, EvidenceKind

# Collector names match the graph node ids, so evidence is attributable to the
# agent that produced it when it is shown in the console.
RESEARCH = "research_analyst"
FINANCIAL = "financial_analyst"
NEWS = "news_analyst"
COMPLIANCE = "compliance_analyst"


def _text(value: Any, limit: int = 400) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def from_baseline(baseline: Any, subject: str) -> List[Evidence]:
    """The corporate identity the research agent established."""
    if not isinstance(baseline, dict):
        return []

    summary = _text(baseline.get("business_summary"))
    if not summary:
        return []

    return [
        Evidence(
            summary=f"Corporate baseline for {baseline.get('entity_name') or subject}",
            detail=summary,
            kind=EvidenceKind.BASELINE,
            source="Research agent (LLM baseline resolver)",
            collector=RESEARCH,
            # An LLM-written summary is interpretation, not a primary record.
            confidence=Confidence.LOW,
            metadata={"ticker": baseline.get("ticker", "")},
        )
    ]


def from_edgar(profile: Any) -> List[Evidence]:
    """SEC filings — the only source the entity wrote about itself."""
    if not isinstance(profile, dict) or not profile.get("matched"):
        return []

    company = profile.get("company") or profile.get("subject") or "the registrant"
    cik = str(profile.get("cik", ""))
    url = str(profile.get("source_url", ""))
    items: List[Evidence] = [
        Evidence(
            summary=f"{company} is an SEC registrant (CIK {cik})",
            detail=(
                f"{profile.get('sic_description', 'Industry not stated')}; incorporated in "
                f"{profile.get('state_of_incorporation', 'n/a')}; "
                f"{profile.get('filings_reviewed', 0)} filings reviewed."
            ),
            kind=EvidenceKind.FILING,
            source="SEC EDGAR submissions",
            collector=RESEARCH,
            confidence=Confidence.HIGH,
            url=url,
            metadata={"cik": cik, "exchanges": profile.get("exchanges", [])},
        )
    ]

    for flag in profile.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        items.append(
            Evidence(
                summary=_text(flag.get("label"), 160),
                detail=(
                    f"Disclosed on {flag.get('date')} via Form {flag.get('form')} "
                    f"item {flag.get('code')}."
                ),
                kind=EvidenceKind.FILING,
                source=f"SEC EDGAR — Form {flag.get('form')} item {flag.get('code')}",
                collector=RESEARCH,
                # A filing is a matter of record, whatever the coverage says.
                confidence=Confidence.HIGH,
                url=url,
                metadata={
                    "code": flag.get("code"),
                    "severity": flag.get("severity"),
                    "date": flag.get("date"),
                    "accession": flag.get("accession", ""),
                },
            )
        )

    return items


# Market metrics worth citing, with how each is rendered for a reader.
_MARKET_FIELDS = (
    ("market_cap", "Market capitalisation", "currency"),
    ("pe_ratio", "Price/earnings ratio", "ratio"),
    ("total_debt", "Total debt", "currency"),
    ("free_cashflow", "Free cash flow", "currency"),
    ("profit_margins", "Profit margin", "percent"),
)


def _format(value: float, style: str) -> str:
    if style == "percent":
        return f"{value:.1%}"
    if style == "ratio":
        return f"{value:.1f}"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= size:
            return f"{value / size:.2f}{unit}"
    return f"{value:,.0f}"


def from_financials(financials: Any, ticker: str = "") -> List[Evidence]:
    """Market metrics, one piece of evidence each so factors can cite precisely."""
    if not isinstance(financials, dict) or financials.get("error"):
        return []

    symbol = financials.get("symbol") or ticker or "the ticker"
    items: List[Evidence] = []

    for field, label, style in _MARKET_FIELDS:
        number = _as_number(financials.get(field))
        if number is None:
            continue
        items.append(
            Evidence(
                summary=f"{label}: {_format(number, style)}",
                detail=f"Reported for {symbol} by the market data feed.",
                kind=EvidenceKind.MARKET_DATA,
                source="yfinance market data",
                collector=FINANCIAL,
                confidence=Confidence.HIGH,
                metadata={"field": field, "value": number, "symbol": symbol},
            )
        )

    return items


def from_osint(findings: Iterable[Dict[str, Any]]) -> List[Evidence]:
    """Open-web reporting. Never better than MEDIUM: coverage is not a finding."""
    items: List[Evidence] = []

    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        title = _text(finding.get("title"), 180)
        url = str(finding.get("url") or finding.get("link") or "")
        if not title and not url:
            continue

        score = _as_number(finding.get("score"))
        host = url.split("/")[2] if url.count("/") >= 2 else "open web"

        items.append(
            Evidence(
                summary=title or f"Open-source item from {host}",
                detail=_text(finding.get("content") or finding.get("snippet"), 500),
                kind=EvidenceKind.OPEN_SOURCE,
                source=host.replace("www.", ""),
                collector=NEWS,
                confidence=Confidence.MEDIUM if (score or 0) >= 0.7 else Confidence.LOW,
                url=url,
                metadata={"relevance": score},
            )
        )

    return items


def from_compliance(brief: Any) -> List[Evidence]:
    """Retrieved obligations, cited back to framework and page."""
    if not isinstance(brief, dict):
        return []

    items: List[Evidence] = []
    for obligation in brief.get("obligations") or []:
        if not isinstance(obligation, dict):
            continue
        framework = obligation.get("framework") or "Internal policy"
        page = obligation.get("page")
        items.append(
            Evidence(
                summary=f"{obligation.get('control', 'Control')} — {framework}",
                detail=_text(obligation.get("requirement"), 500),
                kind=EvidenceKind.POLICY,
                source=f"{framework}{f', p.{page}' if page else ''}",
                collector=COMPLIANCE,
                # Retrieved verbatim from an indexed framework, then summarised.
                confidence=Confidence.MEDIUM,
                url=str(obligation.get("url", "")),
                metadata={"severity": obligation.get("severity"), "page": page},
            )
        )

    return items


def from_sanctions(screening: Any) -> List[Evidence]:
    """The watchlist screen — a determinate result either way.

    A clean screen is evidence too: it is what lets the verdict say "no exposure"
    rather than staying silent on the question.
    """
    if not isinstance(screening, dict) or not screening.get("screened"):
        return []

    status = str(screening.get("status", "UNAVAILABLE"))
    matches = screening.get("matches") or []
    providers = [
        provider.get("label")
        for provider in screening.get("providers") or []
        if isinstance(provider, dict) and provider.get("available")
    ]
    source = ", ".join(providers) or "OFAC SDN list"

    if status == "CLEAR":
        return [
            Evidence(
                summary=f"No watchlist match for {screening.get('subject', 'the subject')}",
                detail=f"Screened against {screening.get('list_size', 0):,} designated entries.",
                kind=EvidenceKind.WATCHLIST,
                source=source,
                collector=COMPLIANCE,
                confidence=Confidence.HIGH,
                metadata={"status": status},
            )
        ]

    items: List[Evidence] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        items.append(
            Evidence(
                summary=f"Watchlist match: {_text(match.get('name'), 160)}",
                detail=(
                    f"Programme {match.get('program', 'n/a')}; matched on "
                    f"{match.get('matched_on', 'name')} at "
                    f"{float(match.get('confidence', 0)) * 100:.0f}% confidence."
                ),
                kind=EvidenceKind.WATCHLIST,
                source=str(match.get("source") or source),
                collector=COMPLIANCE,
                confidence=Confidence.HIGH if status == "HIT" else Confidence.MEDIUM,
                url=str(match.get("url", "")),
                metadata={
                    "status": status,
                    "program": match.get("program"),
                    "match_confidence": match.get("confidence"),
                },
            )
        )

    return items
