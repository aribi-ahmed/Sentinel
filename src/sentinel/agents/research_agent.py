"""Research agent — establishes the corporate baseline.

Reaches the model only through the gateway, so which provider answers is a
configuration question this module never has to know about.
"""

from typing import Any, Dict

from sentinel.llm import AllProvidersFailed, ModelProfile, get_gateway


def fetch_entity_baseline(subject_name: str, ticker: str = "") -> Dict[str, Any]:
    """Returns a short, sourced identity file for the subject."""
    prompt = (
        f"Provide a concise 2-sentence corporate overview for {subject_name} "
        f"(Ticker: {ticker or 'N/A'}). State the main sector and core business focus."
    )

    try:
        # Baseline identity is summarisation, not judgement, so it asks for the
        # cheap profile rather than the reasoning model (§5.1, right-sized models).
        reply = get_gateway().complete(
            prompt,
            profile=ModelProfile.FAST,
            temperature=0.2,
            caller="research_analyst",
        )
        summary = reply.text.strip()
    except AllProvidersFailed as exc:
        summary = f"Research unavailable: {exc}"

    return {
        "entity_name": subject_name,
        "ticker": ticker.upper() if ticker else "N/A",
        "business_summary": summary,
    }
