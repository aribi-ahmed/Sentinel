"""Sanctions and watchlist screening.

Two providers sit behind one result:

* **OFAC SDN (local)** — `datasets/sdn.csv` is the U.S. Treasury Specially
  Designated Nationals list and `datasets/alt.csv` its alias table. Screening
  against them is deterministic and offline: no API, no model, no key.
* **OpenSanctions** — extends coverage to the EU, UN and UK consolidated lists
  plus politically exposed persons. It needs `OPENSANCTIONS_API_KEY`; without
  one the screen still runs, on OFAC alone, and says so.

Either way this is the strongest signal the risk engine has, because a name on
a designated list is categorically different from a company that merely
attracts critical press.
"""

from __future__ import annotations

import csv
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langchain_core.tools import tool

from sentinel.config.settings import BASE_DIR

SDN_PATH = BASE_DIR / "datasets" / "sdn.csv"
ALT_PATH = BASE_DIR / "datasets" / "alt.csv"

# OFAC uses "-0-" as its null marker throughout the fixed-width exports.
NULL_MARKER = "-0-"

# Corporate-form words carry no identifying signal and would otherwise inflate
# the similarity between any two companies. Stripping them is also what lets
# "Gazprom Neft" line up with "Public Joint Stock Company Gazprom Neft".
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "llc", "lp", "llp", "plc", "sa", "ag", "nv", "bv", "gmbh",
    "oy", "ab", "as", "spa", "srl", "pte", "pty", "sarl", "sas", "kg", "kft",
    "joint", "stock", "jointstock", "public", "open", "closed", "ojsc", "pjsc",
    "jsc", "ooo", "oao", "zao", "pao", "fze", "fzc", "fzco", "dmcc", "llp",
    "holdings", "holding", "group", "the", "and", "of",
}

# Below this, two names are simply different companies.
_MATCH_FLOOR = 0.86

OPENSANCTIONS_URL = "https://api.opensanctions.org/match/default"
OPENSANCTIONS_TIMEOUT = 20.0
# OpenSanctions returns a 0-1 score of its own; anything under this is noise.
_OPENSANCTIONS_FLOOR = 0.70

# Worst-first, so merging two providers is a max() over this ordering.
_STATUS_RANK = {"UNAVAILABLE": -1, "CLEAR": 0, "POSSIBLE_MATCH": 1, "HIT": 2}

# Topic codes OpenSanctions attaches to an entity, in reader-facing words.
_TOPIC_LABELS = {
    "sanction": "Sanctioned",
    "sanction.linked": "Linked to a sanctioned party",
    "role.pep": "Politically exposed person",
    "role.rca": "Close associate of a PEP",
    "crime.fin": "Financial crime",
    "crime.terror": "Terrorism",
    "crime.traffick": "Trafficking",
    "export.control": "Export controlled",
    "debarment": "Debarred from public contracts",
}

# OpenSanctions' default collection is broader than a watchlist: it also carries
# corporate registries and research datasets, and those entries come back with a
# perfect name score for any well-known company. Alphabet matched at 1.0 on
# `gem_energy_ownership` and Microsoft at 1.0 on `corp.public` / `reg.warn` —
# both were reported as sanctions HITs and scored ELEVATED. A name appearing in
# a registry is not a designation, so an entry only counts when it carries a
# topic that actually asserts one. These are the topics above: everything the
# module already considered worth naming to a reader.
_DESIGNATION_TOPICS = frozenset(_TOPIC_LABELS)

# OpenSanctions' `score` is its own fuzzy name score, and it rates "Alphabet Inc."
# against the genuinely debarred "Alphabet International DMCC" at 0.848 — a real
# designation, but a different company. Its `match` flag does not help: it came
# back True on every result observed, noise included. So a designated entry must
# also survive this module's own normalised-name comparison, the same measure the
# OFAC screen uses. Against the golden subjects the two populations separate
# cleanly: the closest wrong name scores 0.583 (Alphabet International DMCC) and
# the weakest right one 0.778 (Rosneft Oil Company), so the floor sits between
# them with room on both sides.
#
# The cost is that a designated *affiliate* whose name diverges — Rosneft Trading
# S.A. at 0.538 — no longer escalates the subject on its own. That is deliberate:
# sharing a name fragment with a sanctioned party is not the same finding as being
# one, and the entity itself still matches on its own entry.
_NAME_AGREEMENT_FLOOR = 0.70


def _is_designation(topics: List[str]) -> bool:
    """True when a topic asserts a designation rather than mere registry presence.

    Prefix-matched, so a narrower code (`sanction.eu`) still counts against the
    family it belongs to (`sanction`) without needing to be enumerated.
    """
    return any(
        topic == known or topic.startswith(f"{known}.")
        for topic in topics
        for known in _DESIGNATION_TOPICS
    )


def _clean(value: str) -> str:
    value = (value or "").strip()
    return "" if value == NULL_MARKER else value


def _normalise(name: str) -> str:
    """Reduces a name to comparable tokens: lowercase, no punctuation or suffix."""
    lowered = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    tokens = [token for token in lowered.split() if token and token not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


@lru_cache(maxsize=1)
def _load_list() -> List[Dict[str, Any]]:
    """Reads the SDN list and its aliases once, keyed for name comparison."""
    if not SDN_PATH.is_file():
        return []

    entries: Dict[int, Dict[str, Any]] = {}
    with SDN_PATH.open(encoding="latin-1", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 4:
                continue
            try:
                number = int(row[0])
            except ValueError:
                continue
            name = _clean(row[1])
            if not name:
                continue
            entries[number] = {
                "ent_num": number,
                "name": name,
                "normalised": _normalise(name),
                "entity_type": _clean(row[2]) or "entity",
                "program": _clean(row[3]) or "OFAC",
                "remarks": _clean(row[11]) if len(row) > 11 else "",
                "aliases": [],
            }

    if ALT_PATH.is_file():
        with ALT_PATH.open(encoding="latin-1", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) < 4:
                    continue
                try:
                    number = int(row[0])
                except ValueError:
                    continue
                entry = entries.get(number)
                alias = _clean(row[3])
                if entry and alias:
                    entry["aliases"].append({"name": alias, "normalised": _normalise(alias)})

    return list(entries.values())


@lru_cache(maxsize=1)
def _token_index() -> Dict[str, set]:
    """Maps each name token to the records containing it, to narrow screening."""
    index: Dict[str, set] = {}
    for position, entry in enumerate(_load_list()):
        names = [entry["normalised"], *(alias["normalised"] for alias in entry["aliases"])]
        for name in names:
            for token in name.split():
                index.setdefault(token, set()).add(position)
    return index


def _candidates(target: str) -> List[Dict[str, Any]]:
    """Records sharing at least one token with the target name."""
    records = _load_list()
    index = _token_index()
    positions: set = set()
    for token in set(target.split()):
        positions |= index.get(token, set())
    return [records[position] for position in positions]


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_tokens, right_tokens = left.split(), right.split()
    # Containment ("gazprom neft" inside "gazprom neft trading") is meaningful
    # only for multi-word names. A single distinctive word appearing in a longer
    # designation — "Alphabet" in "Alphabet International DMCC" — is a
    # coincidence, so those names have to match outright.
    if len(left_tokens) >= 2 and len(right_tokens) >= 2:
        shorter, longer = sorted((left, right), key=len)
        if re.search(rf"\b{re.escape(shorter)}\b", longer):
            coverage = min(len(left_tokens), len(right_tokens)) / max(len(left_tokens), len(right_tokens))
            return 0.80 + 0.20 * coverage

    return SequenceMatcher(None, left, right).ratio()


def _best_alias(entry: Dict[str, Any], target: str) -> Tuple[float, Optional[str]]:
    best_score = 0.0
    best_name: Optional[str] = None
    for alias in entry["aliases"]:
        score = _similarity(target, alias["normalised"])
        if score > best_score:
            best_score, best_name = score, alias["name"]
    return best_score, best_name


def _describe_topics(topics: List[str]) -> str:
    named = [_TOPIC_LABELS.get(topic, topic) for topic in topics if topic]
    return ", ".join(dict.fromkeys(named))


def screen_opensanctions(subject_name: str, limit: int = 5) -> Dict[str, Any]:
    """Screens the name against the OpenSanctions consolidated database.

    Returns an UNAVAILABLE provider record rather than raising when no key is
    configured, so the local OFAC screen still stands on its own.
    """
    api_key = os.getenv("OPENSANCTIONS_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "UNAVAILABLE",
            "available": False,
            "matches": [],
            "detail": "Set OPENSANCTIONS_API_KEY to add EU, UN, UK and PEP coverage.",
        }

    try:
        response = httpx.post(
            OPENSANCTIONS_URL,
            timeout=OPENSANCTIONS_TIMEOUT,
            headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            json={
                "queries": {
                    "subject": {
                        "schema": "Company",
                        "properties": {"name": [subject_name]},
                    }
                }
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "available": False,
            "matches": [],
            "detail": f"OpenSanctions request failed: {exc}",
        }

    results = ((payload.get("responses") or {}).get("subject") or {}).get("results") or []

    subject_normalised = _normalise(subject_name)

    matches: List[Dict[str, Any]] = []
    for item in results:
        score = float(item.get("score") or 0)
        if score < _OPENSANCTIONS_FLOOR:
            continue
        properties = item.get("properties") or {}
        topics = [str(topic) for topic in (properties.get("topics") or [])]
        if not _is_designation(topics):
            # Registry or research presence only — not a watchlist entry.
            continue
        datasets = [str(name) for name in (item.get("datasets") or [])]
        caption = str(item.get("caption") or "Designated entity")
        if _similarity(subject_normalised, _normalise(caption)) < _NAME_AGREEMENT_FLOOR:
            # Designated, but not this entity — a name neighbour, not the subject.
            continue

        matches.append({
            "name": caption,
            "matched_name": caption,
            "matched_on": "OpenSanctions entity resolution",
            "entity_type": str(item.get("schema") or "entity").lower(),
            "program": _describe_topics(topics) or (", ".join(datasets[:3]) or "OpenSanctions"),
            "remarks": f"Datasets: {', '.join(datasets[:6])}" if datasets else "",
            "confidence": round(score, 3),
            "source": "OpenSanctions",
            "topics": topics,
            "datasets": datasets,
            "url": f"https://www.opensanctions.org/entities/{item.get('id')}/" if item.get("id") else "",
            # OpenSanctions' own assertion, kept per entry so it is only ever read
            # for a result that already cleared the topic and name gates above.
            "asserted": bool(item.get("match")),
        })

    matches.sort(key=lambda match: match["confidence"], reverse=True)
    matches = matches[:limit]

    asserted = any(match["asserted"] for match in matches)

    if not matches:
        status = "CLEAR"
    elif matches[0]["confidence"] >= 0.90 or asserted:
        status = "HIT"
    else:
        status = "POSSIBLE_MATCH"

    return {
        "status": status,
        "available": True,
        "matches": matches,
        "detail": "EU, UN, UK and OFAC consolidated lists plus PEP data.",
    }


def screen_ofac_local(subject_name: str, limit: int = 5) -> Dict[str, Any]:
    """Screens one name against the SDN list, returning every plausible match."""
    target = _normalise(subject_name)
    records = _load_list()

    if not target or not records:
        return {
            "status": "UNAVAILABLE",
            "available": False,
            "matches": [],
            "list_size": len(records),
            "detail": "datasets/sdn.csv was not readable.",
        }

    matches: List[Dict[str, Any]] = []
    for entry in _candidates(target):
        score = _similarity(target, entry["normalised"])
        matched_on, matched_name = "primary name", entry["name"]

        alias_score, alias_name = _best_alias(entry, target)
        if alias_score > score:
            score, matched_on, matched_name = alias_score, "alias", alias_name or entry["name"]

        if score >= _MATCH_FLOOR:
            matches.append({
                "name": entry["name"],
                "matched_name": matched_name,
                "matched_on": matched_on,
                "entity_type": entry["entity_type"],
                "program": entry["program"],
                "remarks": entry["remarks"][:280],
                "confidence": round(score, 3),
                "source": "OFAC SDN",
                "topics": [],
                "datasets": ["us_ofac_sdn"],
                "url": "",
            })

    matches.sort(key=lambda match: match["confidence"], reverse=True)
    matches = matches[:limit]

    if not matches:
        status = "CLEAR"
    elif matches[0]["confidence"] >= 0.97:
        status = "HIT"
    else:
        status = "POSSIBLE_MATCH"

    return {
        "status": status,
        "available": True,
        "matches": matches,
        "list_size": len(records),
        "detail": f"{len(records):,} designated entries held locally.",
    }


def screen_entity(subject_name: str, limit: int = 5) -> Dict[str, Any]:
    """Screens a name against every configured watchlist provider.

    The overall status is the worst returned by any provider that actually ran,
    so an unreachable OpenSanctions never downgrades a clean OFAC screen — but
    it is still reported, because partial coverage is itself worth knowing.
    """
    local = screen_ofac_local(subject_name, limit=limit)
    external = screen_opensanctions(subject_name, limit=limit)

    providers = [
        {"id": "ofac_local", "label": "OFAC SDN (local)", **{key: local[key] for key in ("status", "available", "detail")}},
        {"id": "opensanctions", "label": "OpenSanctions", **{key: external[key] for key in ("status", "available", "detail")}},
    ]

    matches = [*local["matches"], *external["matches"]]
    matches.sort(key=lambda match: match["confidence"], reverse=True)

    ran = [provider for provider in providers if provider["available"]]
    status = max((provider["status"] for provider in ran), key=lambda name: _STATUS_RANK[name]) if ran else "UNAVAILABLE"

    return {
        "screened": bool(ran),
        "subject": subject_name,
        "list_size": local.get("list_size", 0),
        "matches": matches[:limit],
        "status": status,
        "providers": providers,
    }


@tool
def screen_sanctions(subject_name: str) -> Dict[str, Any]:
    """Screens an entity against the OFAC SDN list and OpenSanctions."""
    return screen_entity(subject_name)
