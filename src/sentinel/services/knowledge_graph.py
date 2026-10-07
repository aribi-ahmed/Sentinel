"""Builds the entity relationship graph from collected evidence.

Every node and edge is derived from something a specialist actually retrieved —
an EDGAR registrant record, an OFAC alias table, an organisation named in
reporting. Nothing is inferred from the model's background knowledge, so a
relationship shown here can always be traced to the evidence that produced it.

The graph earns its place through indirect exposure: an entity that screens
clean under its own name may still sit one hop from a designated party through
an alias or a shared programme.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from sentinel.domain.evidence import EvidencePool
from sentinel.domain.intelligence import EdgeKind, GraphNode, KnowledgeGraph, NodeKind

ROOT_ID = "subject"

# Organisation suffixes used to spot company names in free text.
ORG_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,3}\s+"
    r"(?:Inc|Corp|Corporation|Ltd|Limited|LLC|PLC|SA|NV|AG|GmbH|Group|Holdings|Bank))\b"
)

STOP_WORDS = {"the", "a", "an", "and", "of", "for", "in", "on", "to"}

# EDGAR reports the state of incorporation as a two-letter code. Shown raw it is
# meaningless to a reader, and "DE" in particular carries information worth
# stating: most US public companies incorporate in Delaware for its corporate
# case law, so incorporation elsewhere is the notable case.
JURISDICTIONS = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "E9": "Cayman Islands", "D0": "Bermuda", "N4": "Netherlands",
    "L2": "United Kingdom", "F4": "Canada", "K3": "Ireland", "V8": "Switzerland",
}


def _jurisdiction_label(code: str) -> Tuple[str, str]:
    """Returns the readable name and a one-line note for a jurisdiction code."""
    name = JURISDICTIONS.get(code.upper())
    if not name:
        return code, "State or country of incorporation"

    if code.upper() == "DE":
        note = "State of incorporation - the default choice for US public companies"
    elif name in ("Cayman Islands", "Bermuda", "Ireland", "Switzerland", "Netherlands"):
        note = "Country of incorporation - a jurisdiction commonly used for tax structuring"
    else:
        note = "State or country of incorporation"
    return name, note


def _slug(prefix: str, value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return f"{prefix}_{cleaned}"[:64] or prefix


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return str(value or "")


def _add_registrant(graph: KnowledgeGraph, edgar: Dict[str, Any], pool: EvidencePool) -> None:
    if not edgar.get("matched"):
        return

    filing_ids = tuple(item.id for item in pool if item.kind.value == "filing")[:2]

    company = str(edgar.get("company") or "").strip()
    cik = str(edgar.get("cik") or "").strip()
    if company and cik:
        reviewed = edgar.get("filings_reviewed")
        flags = len(edgar.get("flags") or [])
        parts = [f"SEC registrant, CIK {cik}"]
        if isinstance(reviewed, int) and reviewed:
            parts.append(f"{reviewed:,} filings reviewed")
        parts.append(
            f"{flags} supervisory event(s) disclosed" if flags else "no supervisory events disclosed"
        )

        node = graph.add_node(GraphNode(
            id=_slug("cik", cik),
            label=company,
            kind=NodeKind.REGISTRANT,
            detail=" · ".join(parts),
            flagged=flags > 0,
        ))
        graph.connect(ROOT_ID, node.id, EdgeKind.REGISTERED_AS, filing_ids)

    state = str(edgar.get("state_of_incorporation") or "").strip()
    if state:
        name, note = _jurisdiction_label(state)
        node = graph.add_node(GraphNode(
            id=_slug("jur", state),
            label=name,
            kind=NodeKind.JURISDICTION,
            detail=f"{note} ({state.upper()})",
        ))
        graph.connect(ROOT_ID, node.id, EdgeKind.INCORPORATED_IN, filing_ids)

    sector = str(edgar.get("sic_description") or "").strip()
    if sector:
        code = str(edgar.get("sic") or "").strip()
        node = graph.add_node(GraphNode(
            id=_slug("sic", sector),
            label=sector,
            kind=NodeKind.SECTOR,
            detail=(
                f"Industry classification assigned by the SEC (SIC {code})"
                if code else "Industry classification assigned by the SEC"
            ),
        ))
        graph.connect(ROOT_ID, node.id, EdgeKind.OPERATES_IN, filing_ids)

    fiscal = str(edgar.get("fiscal_year_end") or "").strip()
    for exchange in edgar.get("exchanges") or []:
        name = str(exchange).strip()
        if not name:
            continue
        detail = "Exchange the securities are admitted to trading on"
        if fiscal:
            detail += f" · fiscal year ends {fiscal[:2]}/{fiscal[2:]}" if len(fiscal) == 4 else ""
        node = graph.add_node(GraphNode(
            id=_slug("exch", name),
            label=name,
            kind=NodeKind.ORGANISATION,
            detail=detail,
        ))
        graph.connect(ROOT_ID, node.id, EdgeKind.LISTED_ON, filing_ids)


def _add_screening(graph: KnowledgeGraph, sanctions: Dict[str, Any], pool: EvidencePool) -> None:
    watchlist_ids = tuple(item.id for item in pool if item.kind.value == "watchlist")[:3]

    for match in (sanctions.get("matches") or [])[:6]:
        if not isinstance(match, dict):
            continue

        name = str(match.get("name") or match.get("matched_name") or "").strip()
        if not name:
            continue

        confidence = match.get("confidence")
        source = str(match.get("source") or match.get("list") or "watchlist").strip()
        detail = (
            f"Designated entry on {source}, matched at {confidence:.0%} confidence"
            if isinstance(confidence, (int, float))
            else f"Designated entry on {source}"
        )

        node = graph.add_node(GraphNode(
            id=_slug("wl", name),
            label=name,
            kind=NodeKind.WATCHLIST,
            detail=detail,
            flagged=True,
        ))
        graph.connect(ROOT_ID, node.id, EdgeKind.ALSO_KNOWN_AS, watchlist_ids)

        for programme in (match.get("programs") or match.get("programmes") or [])[:3]:
            label = str(programme).strip()
            if not label:
                continue
            prog = graph.add_node(GraphNode(
                id=_slug("prog", label),
                label=label,
                kind=NodeKind.PROGRAMME,
                detail=f"Sanctions programme under which {name} is designated",
                flagged=True,
            ))
            graph.connect(node.id, prog.id, EdgeKind.DESIGNATED_UNDER, watchlist_ids)


def _add_reported_organisations(
    graph: KnowledgeGraph,
    news: Any,
    subject: str,
    pool: EvidencePool,
) -> None:
    text = _as_text(news)
    if not text:
        return

    open_ids = tuple(item.id for item in pool if item.kind.value == "open_source")[:2]
    subject_tokens = {t.lower() for t in subject.split() if t.lower() not in STOP_WORDS}

    seen: set = set()
    for match in ORG_PATTERN.finditer(text):
        name = " ".join(match.group(1).split())
        key = name.lower()

        if key in seen or len(name) < 6:
            continue
        # Skip anything that is just the subject under another spelling.
        if subject_tokens and subject_tokens & {t.lower() for t in name.split()}:
            continue
        seen.add(key)

        node = graph.add_node(GraphNode(
            id=_slug("org", name),
            label=name,
            kind=NodeKind.ORGANISATION,
            detail=(
                f"Named alongside {subject} in open-source reporting; "
                "co-mention is not evidence of a relationship"
            ),
        ))
        graph.connect(ROOT_ID, node.id, EdgeKind.MENTIONED_WITH, open_ids)

        if len(seen) >= 8:
            break


def build_graph(
    *,
    subject: str,
    ticker: str = "",
    edgar: Optional[Dict[str, Any]] = None,
    sanctions: Optional[Dict[str, Any]] = None,
    news: Any = None,
    evidence: Optional[EvidencePool] = None,
) -> KnowledgeGraph:
    """Assembles the relationship graph for one investigation."""
    pool = evidence if evidence is not None else EvidencePool()
    label = subject or ticker or "Subject"

    graph = KnowledgeGraph(root=ROOT_ID)
    graph.add_node(GraphNode(
        id=ROOT_ID,
        label=label,
        kind=NodeKind.SUBJECT,
        detail=(
            f"Subject of this investigation · ticker {ticker.upper()}"
            if ticker else "Subject of this investigation · no listed ticker"
        ),
    ))

    _add_registrant(graph, edgar or {}, pool)
    _add_screening(graph, sanctions or {}, pool)
    _add_reported_organisations(graph, news, label, pool)

    return graph
