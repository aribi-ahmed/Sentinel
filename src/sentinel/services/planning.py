"""Routing rules — which specialists this investigation actually needs.

§6.2 is explicit about where the line sits: *"Not everything deserves to be an
LLM call: routing that can be a rule should be a rule, because rules are free,
fast and testable."*

Every rule here answers a question that has a determinate answer — is there a
ticker to look up, is a search key configured — so none of them needs a model.
A rule that genuinely required judgement would be a different matter, and the
place to add it is `plan_investigation`; nothing else would change.

The reasons are written for the reader of the final report, not for a log file.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sentinel.domain.planning import InvestigationPlan, RoutingDecision

RESEARCH = "research_analyst"
FINANCIAL = "financial_analyst"
NEWS = "news_analyst"
COMPLIANCE = "compliance_analyst"

SPECIALISTS = (RESEARCH, FINANCIAL, NEWS, COMPLIANCE)


def plan_investigation(
    subject_name: str,
    ticker: str = "",
    *,
    settings: Optional[Any] = None,
) -> InvestigationPlan:
    """Decides which specialists to engage for one request."""
    if settings is None:
        from sentinel.config.settings import settings as loaded

        settings = loaded

    subject = (subject_name or "").strip()
    symbol = (ticker or "").strip()

    decisions = [
        # The baseline identity is what every other specialist is scored
        # against, so it is never skipped.
        RoutingDecision(
            agent=RESEARCH,
            engaged=bool(subject),
            reason=(
                "Establishes the identity every other specialist is scored against."
                if subject
                else "No subject name supplied, so there is nothing to research."
            ),
        ),
        # Market data is keyed on the ticker. Without one there is nothing to
        # fetch, and running the agent would only produce an empty result that
        # the scorer would then have to treat as missing evidence.
        RoutingDecision(
            agent=FINANCIAL,
            engaged=bool(symbol),
            reason=(
                f"Ticker {symbol.upper()} supplied, so market metrics can be retrieved."
                if symbol
                else "No ticker supplied; market data cannot be retrieved for an unlisted entity."
            ),
        ),
        RoutingDecision(
            agent=NEWS,
            engaged=bool(subject) and bool(getattr(settings, "TAVILY_API_KEY", "")),
            reason=(
                "Open-source coverage is relevant to every entity."
                if subject and getattr(settings, "TAVILY_API_KEY", "")
                else "Open-source search is unavailable: TAVILY_API_KEY is not configured."
                if subject
                else "No subject name supplied, so there is nothing to search for."
            ),
        ),
        # Screening and policy retrieval apply to any named entity, and the
        # sanctions list is local, so this never depends on a key.
        RoutingDecision(
            agent=COMPLIANCE,
            engaged=bool(subject),
            reason=(
                "Watchlist screening and policy retrieval apply to every entity."
                if subject
                else "No subject name supplied, so there is nothing to screen."
            ),
        ),
    ]

    return InvestigationPlan(decisions=tuple(decisions))


MEMORY = "memory"


def route_from_plan(plan_payload: Dict[str, Any]) -> list:
    """Turns a serialised plan into the node list LangGraph should fan out to.

    Returning a list from a conditional edge is what triggers parallel
    execution; an empty list would strand the graph, so the supervisor is
    targeted directly when no specialist qualifies.

    Memory joins every run that has a subject at all: it queries the local
    ledger rather than an external source, so there is no cost or availability
    reason to skip it, and a prior verdict is context for any investigation.
    """
    plan = InvestigationPlan.from_dict(plan_payload)
    engaged = [agent for agent in plan.engaged if agent in SPECIALISTS]
    if not engaged:
        return ["supervisor"]
    return [*engaged, MEMORY]
