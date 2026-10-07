"""Cross-investigation recall.

Every other agent starts from nothing. This one reads the audit ledger for
earlier reviews of the same entity, so a verdict can be placed against the
record rather than delivered in isolation — a second ELEVATED rating means
something different from a first one.

Matching is on normalised name or exact ticker. Fuzzy matching is deliberately
avoided: pulling in the history of a differently named entity would corrupt the
comparison it exists to provide.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from sentinel.domain.intelligence import EntityHistory, PriorReview

MAX_REVIEWS = 25

LEGAL_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "llc", "plc", "sa", "nv", "ag", "gmbh", "group", "holdings",
)


def normalise(name: str) -> str:
    """Strips punctuation and legal suffixes so spellings compare equal."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    tokens = [t for t in cleaned.split() if t and t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def _to_review(row: Any) -> Optional[PriorReview]:
    created = getattr(row, "created_at", None)
    if created is None:
        return None

    status = getattr(row, "status", None)
    return PriorReview(
        investigation_id=str(getattr(row, "id", "")),
        band=str(getattr(row, "risk_level", "") or ""),
        reviewed_at=created,
        status=getattr(status, "value", str(status or "")),
        approved=getattr(row, "human_approved", None),
    )


def recall(subject: str, ticker: str = "", *, exclude_id: str = "") -> EntityHistory:
    """Returns prior reviews of the entity, newest first.

    Never raises: an unreachable ledger degrades to an empty history, because a
    missing memory must not stop an investigation from completing.
    """
    label = subject or ticker
    target = normalise(subject)
    ticker_key = (ticker or "").strip().upper()

    if not target and not ticker_key:
        return EntityHistory(subject=label)

    try:
        from sentinel.database.crud import fetch_all_investigations
        from sentinel.database.db import SessionLocal

        session = SessionLocal()
        try:
            rows = fetch_all_investigations(db=session)
        finally:
            session.close()
    except Exception:
        return EntityHistory(subject=label)

    matches: List[PriorReview] = []
    for row in rows:
        if exclude_id and str(getattr(row, "id", "")) == exclude_id:
            continue

        row_ticker = (getattr(row, "ticker", "") or "").strip().upper()
        same_ticker = bool(ticker_key) and row_ticker == ticker_key
        same_name = bool(target) and normalise(getattr(row, "subject_name", "")) == target

        if not (same_ticker or same_name):
            continue

        review = _to_review(row)
        if review is not None:
            matches.append(review)

        if len(matches) >= MAX_REVIEWS:
            break

    matches.sort(key=lambda r: r.reviewed_at, reverse=True)
    return EntityHistory(subject=label, reviews=tuple(matches))
