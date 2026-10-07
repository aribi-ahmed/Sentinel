"""SEC EDGAR filing history.

EDGAR is free and needs no API key — only a descriptive User-Agent, which the
SEC requires and rate-limits on. What it buys is the difference between inferring
legal exposure from press coverage and reading it off the company's own
disclosures: a Form 8-K Item 4.02 means the issuer has told the market its
previously published financials cannot be relied upon, and no amount of
favourable reporting changes that.

Everything here is deterministic. The filings either exist or they do not.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from langchain_core.tools import tool

TICKER_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FILING_INDEX_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40"

TIMEOUT = 20.0
# How far back a filing still counts as a live signal.
RECENT_DAYS = 730


def _user_agent() -> str:
    """SEC policy requires a contact address; make it configurable."""
    return os.getenv("SEC_USER_AGENT", "Sentinel Compliance Research (contact: compliance@sentinel.local)")


def _get_json(url: str) -> Optional[Any]:
    try:
        response = httpx.get(
            url,
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"},
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _normalise(name: str) -> str:
    lowered = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    drop = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
            "limited", "llc", "plc", "holdings", "holding", "group", "the"}
    return " ".join(token for token in lowered.split() if token and token not in drop)


# How long a failed index fetch is remembered before another is attempted. The
# SEC rate-limits by User-Agent, so retrying on the very next call would turn one
# throttle into a burst of them; waiting for ever is worse.
INDEX_RETRY_COOLDOWN = 60.0

_INDEX_CACHE: Dict[str, Any] = {"value": None, "retry_after": 0.0}


def _ticker_index() -> Dict[str, Any]:
    """Maps tickers and normalised names to CIKs. Fetched once, on success.

    This was `@lru_cache(maxsize=1)`, which cached the *failure* too: one
    throttled response and the empty index was returned for the life of the
    process, so every later investigation lost its filing history until the API
    was restarted. Observed in the wild — `fetch_sec_filings` returning
    UNAVAILABLE in 0.06 ms, which is not a network round trip.

    A successful fetch is still cached for ever; the index changes daily and the
    process does not outlive that. A failure is cached only briefly, so the
    collector recovers on its own.
    """
    now = time.monotonic()
    cached = _INDEX_CACHE["value"]
    if cached is not None:
        return cached
    if now < _INDEX_CACHE["retry_after"]:
        return {"by_ticker": {}, "by_name": {}}

    payload = _get_json(TICKER_INDEX_URL)
    by_ticker: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    if not isinstance(payload, dict) or not payload:
        _INDEX_CACHE["retry_after"] = now + INDEX_RETRY_COOLDOWN
        return {"by_ticker": by_ticker, "by_name": by_name}

    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        record = {
            "cik": str(entry.get("cik_str", "")).zfill(10),
            "ticker": str(entry.get("ticker", "")).upper(),
            "title": str(entry.get("title", "")),
        }
        if record["ticker"]:
            by_ticker[record["ticker"]] = record
        key = _normalise(record["title"])
        if key and key not in by_name:
            by_name[key] = record

    index = {"by_ticker": by_ticker, "by_name": by_name}
    _INDEX_CACHE["value"] = index
    return index


def resolve_cik(ticker: str = "", subject_name: str = "") -> Optional[Dict[str, Any]]:
    """Finds the EDGAR registrant for a ticker, falling back to the name."""
    index = _ticker_index()
    if ticker:
        hit = index["by_ticker"].get(ticker.strip().upper())
        if hit:
            return hit

    key = _normalise(subject_name)
    if not key:
        return None
    exact = index["by_name"].get(key)
    if exact:
        return exact

    # A registrant filed as "Alphabet Inc." should still be found from
    # "Alphabet", but only when the query is specific enough to be unambiguous.
    if len(key) >= 5:
        for name, record in index["by_name"].items():
            if name.startswith(f"{key} ") or name == key:
                return record
    return None


# Form 8-K item codes that carry a supervisory signal, with the floor each one
# puts under the regulatory dimension. Codes absent here are routine.
ITEM_SIGNALS: Dict[str, Dict[str, Any]] = {
    "1.03": {"label": "Bankruptcy or receivership", "severity": "severe", "floor": 90},
    "4.02": {"label": "Financials no longer reliable", "severity": "severe", "floor": 72},
    "3.01": {"label": "Delisting or listing-rule failure", "severity": "severe", "floor": 70},
    "1.05": {"label": "Material cybersecurity incident", "severity": "elevated", "floor": 55},
    "4.01": {"label": "Change of certifying accountant", "severity": "elevated", "floor": 50},
    "2.06": {"label": "Material impairment", "severity": "moderate", "floor": 45},
    "2.04": {"label": "Accelerated financial obligation", "severity": "moderate", "floor": 45},
}

# Routine on their own — a large issuer files these constantly — but a cluster of
# them inside the window is itself the signal, so they are counted, not listed.
CHURN_ITEMS: Dict[str, Dict[str, Any]] = {
    "5.02": {"label": "Director or officer departures", "threshold": 6, "floor": 38},
    "1.02": {"label": "Material agreements terminated", "threshold": 4, "floor": 34},
}

# Whole forms that signal distress regardless of item codes.
FORM_SIGNALS: Dict[str, Dict[str, Any]] = {
    "NT 10-K": {"label": "Annual report filed late", "severity": "elevated", "floor": 60},
    "NT 10-Q": {"label": "Quarterly report filed late", "severity": "moderate", "floor": 48},
    "25-NSE": {"label": "Exchange delisting notice", "severity": "severe", "floor": 75},
    "15-12B": {"label": "Deregistration of securities", "severity": "elevated", "floor": 58},
}

ANNUAL_FORMS = {"10-K", "20-F", "40-F", "10-K/A"}


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _analyse(recent: Dict[str, List[Any]]) -> Dict[str, Any]:
    """Extracts supervisory signals from the filing history."""
    forms = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    items = recent.get("items", []) or []
    accessions = recent.get("accessionNumber", []) or []

    today = datetime.now(timezone.utc).date()
    flags: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    churn: Dict[str, Dict[str, Any]] = {}
    latest_annual: Optional[str] = None

    for position, form in enumerate(forms):
        counts[form] = counts.get(form, 0) + 1
        filed = _parse_date(dates[position] if position < len(dates) else "")
        if form in ANNUAL_FORMS and latest_annual is None:
            latest_annual = dates[position] if position < len(dates) else None

        if filed is None or (today - filed).days > RECENT_DAYS:
            continue

        accession = accessions[position] if position < len(accessions) else ""

        signal = FORM_SIGNALS.get(form)
        if signal:
            flags.append({
                "code": form,
                "label": signal["label"],
                "severity": signal["severity"],
                "floor": signal["floor"],
                "date": dates[position],
                "form": form,
                "accession": accession,
            })

        raw_items = items[position] if position < len(items) else ""
        for code in [part.strip() for part in str(raw_items).split(",") if part.strip()]:
            if code in CHURN_ITEMS:
                entry = churn.setdefault(code, {"count": 0, "latest": dates[position]})
                entry["count"] += 1
                entry["latest"] = max(entry["latest"], dates[position])
                continue

            signal = ITEM_SIGNALS.get(code)
            if not signal:
                continue
            flags.append({
                "code": code,
                "label": signal["label"],
                "severity": signal["severity"],
                "floor": signal["floor"],
                "date": dates[position],
                "form": form,
                "accession": accession,
            })

    for code, entry in churn.items():
        rule = CHURN_ITEMS[code]
        if entry["count"] < rule["threshold"]:
            continue
        flags.append({
            "code": code,
            "label": f"{rule['label']} ({entry['count']} in 24 months)",
            "severity": "moderate",
            "floor": rule["floor"],
            "date": entry["latest"],
            "form": "8-K",
            "accession": "",
        })

    flags.sort(key=lambda flag: (-flag["floor"], flag["date"]))

    annual_age = None
    parsed_annual = _parse_date(latest_annual or "")
    if parsed_annual:
        annual_age = (today - parsed_annual).days

    return {
        "flags": flags[:8],
        "counts": counts,
        "routine": {code: entry["count"] for code, entry in churn.items()},
        "latest_annual": latest_annual,
        "annual_age_days": annual_age,
        "filings_reviewed": len(forms),
    }


def fetch_edgar_profile(subject_name: str, ticker: str = "") -> Dict[str, Any]:
    """Looks the entity up in EDGAR and summarises its filing history.

    `matched: False` alone is ambiguous, and the ambiguity is the dangerous kind:
    "this entity is not an SEC registrant" supports a favourable verdict, while
    "EDGAR could not be reached" supports nothing at all. `reachable` separates
    them so the registry can classify the second as UNAVAILABLE rather than
    EMPTY — §2.7's distinction, applied to the source that most often fails,
    since the SEC rate-limits by User-Agent.
    """
    index_loaded = bool(_ticker_index()["by_ticker"])
    if not index_loaded:
        return {
            "matched": False,
            "reachable": False,
            "subject": subject_name,
            "reason": (
                "The EDGAR ticker index could not be retrieved, so no lookup was "
                "performed. This is not a finding about the entity."
            ),
            "flags": [],
            "counts": {},
        }

    record = resolve_cik(ticker=ticker, subject_name=subject_name)
    if not record:
        return {
            "matched": False,
            "reachable": True,
            "subject": subject_name,
            "reason": (
                "No SEC registrant matched. The EDGAR ticker index covers currently "
                "listed US issuers, so foreign, private and delisted entities will not appear."
            ),
            "flags": [],
            "counts": {},
        }

    submissions = _get_json(SUBMISSIONS_URL.format(cik=record["cik"]))
    if not isinstance(submissions, dict):
        return {
            "matched": False,
            "reachable": False,
            "subject": subject_name,
            "cik": record["cik"],
            "reason": (
                f"The entity resolved to CIK {record['cik']}, but the EDGAR submissions "
                "endpoint was unreachable, so its filing history was never read."
            ),
            "flags": [],
            "counts": {},
        }

    analysis = _analyse((submissions.get("filings") or {}).get("recent") or {})

    return {
        "matched": True,
        "subject": subject_name,
        "cik": record["cik"],
        "company": submissions.get("name") or record["title"],
        "ticker": record["ticker"],
        "sic": submissions.get("sic", ""),
        "sic_description": submissions.get("sicDescription", ""),
        "state_of_incorporation": submissions.get("stateOfIncorporation", ""),
        "fiscal_year_end": submissions.get("fiscalYearEnd", ""),
        "exchanges": sorted(set(submissions.get("exchanges") or [])),
        "source_url": FILING_INDEX_URL.format(cik=record["cik"].lstrip("0")),
        **analysis,
    }


@tool
def fetch_sec_filings(subject_name: str, ticker: str = "") -> Dict[str, Any]:
    """Retrieves an entity's SEC EDGAR filing history and supervisory flags."""
    return fetch_edgar_profile(subject_name=subject_name, ticker=ticker)
