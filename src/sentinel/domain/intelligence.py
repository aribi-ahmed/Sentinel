"""Entity history, relationship graph and fraud-signal convergence.

Three analytical layers that sit above raw evidence:

* `EntityHistory` — what previous investigations of this entity concluded.
* `KnowledgeGraph` — the entities connected to the subject, and how.
* `FraudAssessment` — the co-occurrence of red flags, scored jointly.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from sentinel.domain.evidence import new_id, utc_now


# --------------------------------------------------------------------------- #
#  Entity history                                                              #
# --------------------------------------------------------------------------- #

class HistoryTrend(str, Enum):
    """Direction of travel between the previous review and this one."""

    FIRST_REVIEW = "first_review"
    DETERIORATING = "deteriorating"
    STABLE = "stable"
    IMPROVING = "improving"

    @property
    def label(self) -> str:
        return {
            "first_review": "First review",
            "deteriorating": "Deteriorating",
            "stable": "Stable",
            "improving": "Improving",
        }[self.value]


BAND_RANK = {"MINIMAL": 0, "LOW": 1, "MODERATE": 2, "ELEVATED": 3, "SEVERE": 4}


@dataclass(frozen=True, slots=True)
class PriorReview:
    """One earlier investigation of the same entity."""

    investigation_id: str
    band: str
    reviewed_at: datetime
    status: str = ""
    approved: Optional[bool] = None

    @property
    def rank(self) -> int:
        return BAND_RANK.get(self.band.upper(), -1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investigation_id": self.investigation_id,
            "band": self.band,
            "reviewed_at": self.reviewed_at.isoformat(),
            "status": self.status,
            "approved": self.approved,
        }


@dataclass(frozen=True, slots=True)
class EntityHistory:
    """Everything on record about an entity before this investigation."""

    subject: str
    reviews: Tuple[PriorReview, ...] = ()
    checked_at: datetime = field(default_factory=utc_now)

    @property
    def count(self) -> int:
        return len(self.reviews)

    @property
    def is_first_review(self) -> bool:
        return not self.reviews

    @property
    def latest(self) -> Optional[PriorReview]:
        return self.reviews[0] if self.reviews else None

    @property
    def trend(self) -> HistoryTrend:
        if len(self.reviews) < 1:
            return HistoryTrend.FIRST_REVIEW
        ranked = [r for r in self.reviews if r.rank >= 0]
        if len(ranked) < 2:
            return HistoryTrend.STABLE
        if ranked[0].rank > ranked[-1].rank:
            return HistoryTrend.DETERIORATING
        if ranked[0].rank < ranked[-1].rank:
            return HistoryTrend.IMPROVING
        return HistoryTrend.STABLE

    @property
    def rejection_count(self) -> int:
        """Times an analyst declined to approve this entity."""
        return sum(1 for r in self.reviews if r.approved is False)

    def compare(self, band: str) -> str:
        """One line placing the current verdict against the record."""
        if self.is_first_review:
            return f"No prior review of {self.subject} on record."

        previous = self.latest
        assert previous is not None
        moved = BAND_RANK.get(band.upper(), -1) - previous.rank
        when = previous.reviewed_at.strftime("%d %b %Y")

        if moved > 0:
            direction = f"up from {previous.band}"
        elif moved < 0:
            direction = f"down from {previous.band}"
        else:
            direction = f"unchanged at {previous.band}"

        line = f"Review {self.count + 1} of {self.subject}: {direction} on {when}."
        if self.rejection_count:
            line += f" {self.rejection_count} prior verdict(s) were declined by an analyst."
        return line

    def to_dict(self, band: str = "") -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "count": self.count,
            "is_first_review": self.is_first_review,
            "trend": self.trend.value,
            "trend_label": self.trend.label,
            "rejection_count": self.rejection_count,
            "summary": self.compare(band) if band else "",
            "reviews": [r.to_dict() for r in self.reviews],
            "checked_at": self.checked_at.isoformat(),
        }


# --------------------------------------------------------------------------- #
#  Knowledge graph                                                             #
# --------------------------------------------------------------------------- #

class NodeKind(str, Enum):
    SUBJECT = "subject"
    ALIAS = "alias"
    REGISTRANT = "registrant"
    JURISDICTION = "jurisdiction"
    SECTOR = "sector"
    WATCHLIST = "watchlist"
    ORGANISATION = "organisation"
    PROGRAMME = "programme"


class EdgeKind(str, Enum):
    ALSO_KNOWN_AS = "also_known_as"
    REGISTERED_AS = "registered_as"
    INCORPORATED_IN = "incorporated_in"
    OPERATES_IN = "operates_in"
    DESIGNATED_UNDER = "designated_under"
    LISTED_ON = "listed_on"
    MENTIONED_WITH = "mentioned_with"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    label: str
    kind: NodeKind
    detail: str = ""
    flagged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind.value,
            "detail": self.detail,
            "flagged": self.flagged,
        }


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    kind: EdgeKind
    evidence_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "label": self.kind.label,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass
class KnowledgeGraph:
    """Entities connected to the subject, built from collected evidence."""

    root: str = ""
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> GraphNode:
        if not any(existing.id == node.id for existing in self.nodes):
            self.nodes.append(node)
        return node

    def connect(self, source: str, target: str, kind: EdgeKind, evidence_ids: Tuple[str, ...] = ()) -> None:
        if source == target:
            return
        known = {n.id for n in self.nodes}
        if source not in known or target not in known:
            return
        if any(e.source == source and e.target == target and e.kind is kind for e in self.edges):
            return
        self.edges.append(GraphEdge(source, target, kind, evidence_ids))

    @property
    def flagged_nodes(self) -> List[GraphNode]:
        return [n for n in self.nodes if n.flagged]

    def neighbours(self, node_id: str) -> List[GraphNode]:
        linked = {e.target for e in self.edges if e.source == node_id}
        linked |= {e.source for e in self.edges if e.target == node_id}
        return [n for n in self.nodes if n.id in linked]

    def summary(self) -> str:
        if not self.nodes:
            return "No connected entities were resolved."
        flagged = self.flagged_nodes
        line = f"{len(self.nodes)} entities across {len(self.edges)} relationships."
        if flagged:
            names = ", ".join(n.label for n in flagged[:3])
            line += f" {len(flagged)} carry a watchlist association: {names}."
        return line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "flagged_count": len(self.flagged_nodes),
            "summary": self.summary(),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


# --------------------------------------------------------------------------- #
#  Fraud signals                                                               #
# --------------------------------------------------------------------------- #

class SignalCategory(str, Enum):
    """The three conditions that together characterise fraud risk.

    Any one on its own is common and usually benign. Their co-occurrence is
    what distinguishes a distressed company from a misreporting one.
    """

    PRESSURE = "pressure"          # incentive to misstate: losses, leverage, covenants
    OPPORTUNITY = "opportunity"    # weak oversight: auditor churn, officer exits
    DISCLOSURE = "disclosure"      # the misstatement surfacing: restatements, late filings

    @property
    def label(self) -> str:
        return {
            "pressure": "Financial pressure",
            "opportunity": "Control weakness",
            "disclosure": "Reporting failure",
        }[self.value]


@dataclass(frozen=True, slots=True)
class FraudSignal:
    """One observed red flag.

    `disclosed` separates a fact the entity reported to its regulator from an
    allegation appearing in the press. Both are worth recording; only the first
    can carry a verdict on its own.
    """

    id: str
    label: str
    category: SignalCategory
    weight: float
    detail: str
    evidence_ids: Tuple[str, ...] = ()
    observed_at: str = ""
    disclosed: bool = False

    @property
    def provenance(self) -> str:
        return "Regulatory disclosure" if self.disclosed else "Open-source reporting"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category.value,
            "category_label": self.category.label,
            "weight": self.weight,
            "detail": self.detail,
            "evidence_ids": list(self.evidence_ids),
            "observed_at": self.observed_at,
            "disclosed": self.disclosed,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class FraudPattern:
    """A combination of signals that means more together than apart."""

    id: str
    label: str
    detail: str
    signal_ids: Tuple[str, ...]
    uplift: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "signal_ids": list(self.signal_ids),
            "uplift": self.uplift,
        }


@dataclass(frozen=True, slots=True)
class FraudAssessment:
    """Fraud exposure derived from signal convergence."""

    score: float
    signals: Tuple[FraudSignal, ...] = ()
    patterns: Tuple[FraudPattern, ...] = ()
    id: str = field(default_factory=lambda: new_id("fraud"))
    assessed_at: datetime = field(default_factory=utc_now)

    @property
    def disclosed_signals(self) -> Tuple[FraudSignal, ...]:
        """Signals the entity itself reported to a regulator."""
        return tuple(s for s in self.signals if s.disclosed)

    @property
    def disclosed_score(self) -> float:
        """Score attributable to regulatory disclosures alone.

        The composite escalation path reads this rather than the headline score:
        press allegations should raise a verdict, but only an adjudicated or
        self-reported fact should be able to drive it on its own.
        """
        disclosed = self.disclosed_signals
        if not disclosed:
            return 0.0
        ordered = sorted(disclosed, key=lambda s: s.weight, reverse=True)
        return min(100.0, ordered[0].weight + sum(s.weight * 0.45 for s in ordered[1:]))

    @property
    def categories(self) -> Tuple[SignalCategory, ...]:
        seen: List[SignalCategory] = []
        for signal in self.signals:
            if signal.category not in seen:
                seen.append(signal.category)
        return tuple(seen)

    @property
    def band(self) -> str:
        if self.score >= 70:
            return "SEVERE"
        if self.score >= 45:
            return "ELEVATED"
        if self.score >= 20:
            return "MODERATE"
        if self.score > 0:
            return "LOW"
        return "MINIMAL"

    def signals_in(self, category: SignalCategory) -> Tuple[FraudSignal, ...]:
        return tuple(s for s in self.signals if s.category is category)

    def summary(self) -> str:
        if not self.signals:
            return "No fraud indicators were identified in the reviewed disclosures."

        disclosed = len(self.disclosed_signals)
        provenance = (
            f"{disclosed} from regulatory disclosures"
            if disclosed
            else "all from open-source reporting, none from regulatory disclosures"
        )
        line = (
            f"{len(self.signals)} indicator(s) across {len(self.categories)} of 3 "
            f"categories ({provenance}), scoring {self.score:.0f}/100 ({self.band})."
        )
        if self.patterns:
            line += f" {len(self.patterns)} convergence pattern(s): " + "; ".join(
                p.label for p in self.patterns
            ) + "."
        return line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "score": round(self.score, 1),
            "band": self.band,
            "summary": self.summary(),
            "categories": [c.value for c in self.categories],
            "disclosed_score": round(self.disclosed_score, 1),
            "disclosed_count": len(self.disclosed_signals),
            "signals": [s.to_dict() for s in self.signals],
            "patterns": [p.to_dict() for p in self.patterns],
            "assessed_at": self.assessed_at.isoformat(),
        }
