"""Evidence — the atomic sourced fact.

Every claim SENTINEL makes has to bottom out in one of these. The brief is blunt
about it: *"a claim with no evidence reference is a defect, not a feature"*
(§5.1). Giving evidence a stable identity is what turns that principle from an
aspiration into something the API can actually enforce and the reviewer can
click through.

This module imports nothing but the standard library, and that is deliberate:
the dependency rule (§10.6) says the domain never reaches outward. If SQLAlchemy,
LangChain or FastAPI were swapped tomorrow, nothing here would change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


def new_id(prefix: str) -> str:
    """Short, readable, collision-safe identifier — e.g. `ev_9f3a1c2b`."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceKind(str, Enum):
    """What sort of fact this is, which governs how much weight it can carry."""

    FILING = "filing"                # a regulatory disclosure by the entity itself
    MARKET_DATA = "market_data"      # a quantitative market metric
    WATCHLIST = "watchlist"          # a sanctions or watchlist screening result
    POLICY = "policy"                # a retrieved regulatory obligation
    OPEN_SOURCE = "open_source"      # press and other open-web reporting
    BASELINE = "baseline"            # corporate identity and structure


class Confidence(str, Enum):
    """How much the collector trusts the fact, not how bad the fact is."""

    HIGH = "high"        # deterministic: a filing exists, a name matches a list
    MEDIUM = "medium"    # sourced but interpreted
    LOW = "low"          # inferred, single-sourced or stale

    @property
    def weight(self) -> float:
        return {"high": 1.0, "medium": 0.7, "low": 0.4}[self.value]


@dataclass(frozen=True, slots=True)
class Evidence:
    """One sourced fact, attributable to the agent that collected it."""

    summary: str
    kind: EvidenceKind
    source: str
    collector: str
    confidence: Confidence = Confidence.MEDIUM
    id: str = field(default_factory=lambda: new_id("ev"))
    collected_at: datetime = field(default_factory=utc_now)
    url: str = ""
    detail: str = ""
    # Free-form provenance: page number, CIK, ticker, OFAC programme, score.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("Evidence needs a summary; an unlabelled fact cannot be cited.")
        if not self.source.strip():
            raise ValueError("Evidence needs a source; that is what makes it evidence.")

    @property
    def citation(self) -> str:
        """How this fact is referred to in a report."""
        return f"[{self.id}] {self.source}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "kind": self.kind.value,
            "source": self.source,
            "collector": self.collector,
            "confidence": self.confidence.value,
            "collected_at": self.collected_at.isoformat(),
            "url": self.url,
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Evidence":
        collected = data.get("collected_at")
        return cls(
            id=str(data.get("id") or new_id("ev")),
            summary=str(data.get("summary", "")),
            kind=EvidenceKind(data.get("kind", EvidenceKind.OPEN_SOURCE.value)),
            source=str(data.get("source", "")),
            collector=str(data.get("collector", "unknown")),
            confidence=Confidence(data.get("confidence", Confidence.MEDIUM.value)),
            collected_at=datetime.fromisoformat(collected) if isinstance(collected, str) else utc_now(),
            url=str(data.get("url", "")),
            detail=str(data.get("detail", "")),
            metadata=dict(data.get("metadata") or {}),
        )


class EvidencePool:
    """The evidence gathered during one investigation, addressable by id.

    Specialists append; the supervisor and report writer read. Keeping the pool
    behind a small class rather than a bare list is what lets a `RiskFactor`
    hold ids and still be resolved back to full records at the API boundary.
    """

    __slots__ = ("_items",)

    def __init__(self, items: Optional[list[Evidence]] = None) -> None:
        self._items: Dict[str, Evidence] = {item.id: item for item in (items or [])}

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items.values())

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items

    def add(self, evidence: Evidence) -> Evidence:
        self._items[evidence.id] = evidence
        return evidence

    def extend(self, items) -> None:
        for item in items:
            self.add(item)

    def get(self, evidence_id: str) -> Optional[Evidence]:
        return self._items.get(evidence_id)

    def resolve(self, evidence_ids) -> list[Evidence]:
        """Returns the records for the ids that exist, skipping any that do not."""
        return [self._items[eid] for eid in evidence_ids if eid in self._items]

    def by_collector(self, collector: str) -> list[Evidence]:
        return [item for item in self._items.values() if item.collector == collector]

    def by_kind(self, kind: EvidenceKind) -> list[Evidence]:
        return [item for item in self._items.values() if item.kind is kind]

    def to_list(self) -> list[Dict[str, Any]]:
        return [item.to_dict() for item in self._items.values()]

    @classmethod
    def from_list(cls, data) -> "EvidencePool":
        return cls([Evidence.from_dict(item) for item in (data or []) if isinstance(item, dict)])
