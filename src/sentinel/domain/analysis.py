"""Findings, risk factors and the assessment they compose into.

This is the chain §11 asks a reviewer to be able to walk:

    RiskAssessment -> RiskFactor -> Finding -> Evidence -> source

A score that cannot be walked back to a source is exactly the failure mode the
citation rule exists to catch, so `RiskFactor` deliberately cannot be built
without at least naming where it came from.

Standard library only — see the note in `evidence.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sentinel.domain.evidence import EvidencePool, new_id


class Severity(str, Enum):
    """How serious a finding is, independent of how certain we are of it."""

    INFORMATIONAL = "informational"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskBand(str, Enum):
    """The five verdict bands. Ordered, so comparisons are meaningful."""

    MINIMAL = "MINIMAL"
    LOW = "LOW"
    MODERATE = "MODERATE"
    ELEVATED = "ELEVATED"
    SEVERE = "SEVERE"

    @property
    def floor(self) -> int:
        return {"MINIMAL": 0, "LOW": 20, "MODERATE": 40, "ELEVATED": 60, "SEVERE": 80}[self.value]

    @classmethod
    def for_score(cls, score: float) -> "RiskBand":
        band = cls.MINIMAL
        for candidate in cls:
            if score >= candidate.floor:
                band = candidate
        return band

    def __lt__(self, other: "RiskBand") -> bool:  # type: ignore[override]
        if not isinstance(other, RiskBand):
            return NotImplemented
        return self.floor < other.floor


@dataclass(frozen=True, slots=True)
class Finding:
    """An interpreted claim, standing on one or more pieces of evidence."""

    claim: str
    severity: Severity
    author: str
    evidence_ids: Tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("fn"))
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.claim.strip():
            raise ValueError("A finding must state a claim.")

    @property
    def is_grounded(self) -> bool:
        """A finding with no evidence is an assertion, and the critic should catch it."""
        return bool(self.evidence_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "claim": self.claim,
            "severity": self.severity.value,
            "author": self.author,
            "rationale": self.rationale,
            "evidence_ids": list(self.evidence_ids),
            "grounded": self.is_grounded,
        }


@dataclass(frozen=True, slots=True)
class RiskFactor:
    """A named, weighted contributor to the composite score.

    `score` is 0-100 within this factor; `weight` is its share of the composite.
    `evidence_ids` is what makes the number defensible.
    """

    id: str
    label: str
    score: float
    weight: float
    rationale: str
    evidence_ids: Tuple[str, ...] = ()
    finding_ids: Tuple[str, ...] = ()
    assessed: bool = True
    # Per-factor detail: the computed market metrics, the watchlist matches.
    signals: Tuple[Dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 100.0:
            raise ValueError(f"RiskFactor {self.id!r} score {self.score} is outside 0-100.")
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"RiskFactor {self.id!r} weight {self.weight} is outside 0-1.")

    @property
    def band(self) -> RiskBand:
        return RiskBand.for_score(self.score)

    @property
    def contribution(self) -> float:
        """How many points this factor puts into the composite."""
        return round(self.score * self.weight, 2)

    @property
    def is_grounded(self) -> bool:
        return bool(self.evidence_ids or self.signals)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "score": round(self.score, 1),
            "weight": self.weight,
            "band": self.band.value,
            "contribution": self.contribution,
            "rationale": self.rationale,
            "assessed": self.assessed,
            "grounded": self.is_grounded,
            "evidence_ids": list(self.evidence_ids),
            "finding_ids": list(self.finding_ids),
            "signals": [dict(signal) for signal in self.signals],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["RiskFactor"]:
        """Rebuilds a factor from its serialised form.

        Returns `None` on a row that cannot be trusted rather than raising: the
        caller is usually reading a checkpoint, and one malformed factor must
        not make a whole investigation unreadable.
        """
        if not isinstance(data, dict) or not str(data.get("id") or "").strip():
            return None
        try:
            return cls(
                id=str(data["id"]),
                label=str(data.get("label") or data["id"]),
                score=float(data.get("score") or 0.0),
                weight=float(data.get("weight") or 0.0),
                rationale=str(data.get("rationale") or ""),
                evidence_ids=tuple(data.get("evidence_ids") or ()),
                finding_ids=tuple(data.get("finding_ids") or ()),
                assessed=bool(data.get("assessed", True)),
                signals=tuple(dict(sig) for sig in (data.get("signals") or []) if isinstance(sig, dict)),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """The verdict: a composite score decomposed into traceable factors."""

    score: float
    factors: Tuple[RiskFactor, ...]
    summary: str
    confidence: float
    evidence_quality: str = "adequate"
    key_drivers: Tuple[str, ...] = ()
    mitigants: Tuple[str, ...] = ()
    # Set when a categorical finding lifted the score above the weighted average.
    escalated: bool = False
    escalation_note: str = ""
    model_ok: bool = True
    model_error: str = ""

    @property
    def band(self) -> RiskBand:
        return RiskBand.for_score(self.score)

    @property
    def weighted_average(self) -> float:
        """The composite before any escalation — useful for explaining the lift."""
        return round(sum(factor.contribution for factor in self.factors), 1)

    def factor(self, factor_id: str) -> RiskFactor | None:
        return next((f for f in self.factors if f.id == factor_id), None)

    def cited_evidence_ids(self) -> List[str]:
        """Every evidence id the verdict rests on, de-duplicated, in order."""
        seen: Dict[str, None] = {}
        for factor in self.factors:
            for evidence_id in factor.evidence_ids:
                seen.setdefault(evidence_id, None)
        return list(seen)

    def ungrounded_factors(self) -> List[RiskFactor]:
        """Assessed factors carrying no evidence — what a critic agent would flag."""
        return [f for f in self.factors if f.assessed and not f.is_grounded]

    def to_dict(self, pool: EvidencePool | None = None) -> Dict[str, Any]:
        """Serialises the verdict, resolving citations when a pool is supplied."""
        payload: Dict[str, Any] = {
            "score": round(self.score, 1),
            "band": self.band.value,
            "weighted_average": self.weighted_average,
            "confidence": round(self.confidence, 2),
            "summary": self.summary,
            "evidence_quality": self.evidence_quality,
            "key_drivers": list(self.key_drivers),
            "mitigants": list(self.mitigants),
            "escalated": self.escalated,
            "escalation_note": self.escalation_note,
            "model_ok": self.model_ok,
            "model_error": self.model_error or None,
            "dimensions": [factor.to_dict() for factor in self.factors],
        }
        if pool is not None:
            payload["evidence"] = [item.to_dict() for item in pool]
            payload["cited_evidence"] = [
                item.to_dict() for item in pool.resolve(self.cited_evidence_ids())
            ]
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> Optional["RiskAssessment"]:
        """Rebuilds a verdict from its serialised form.

        The critic reads the assessment back through this rather than receiving
        the supervisor's own object. That is deliberate: a reviewer sharing the
        author's working memory is not independent, and round-tripping through
        the record is what catches a verdict that holds together in memory but
        not in what was actually persisted.
        """
        if not isinstance(data, dict) or data.get("score") is None:
            return None

        rebuilt = (RiskFactor.from_dict(row) for row in (data.get("dimensions") or []))
        factors = tuple(f for f in rebuilt if f is not None)
        if not factors:
            return None

        try:
            return cls(
                score=float(data["score"]),
                factors=factors,
                summary=str(data.get("summary") or ""),
                confidence=float(data.get("confidence") or 0.0),
                evidence_quality=str(data.get("evidence_quality") or "adequate"),
                key_drivers=tuple(str(d) for d in (data.get("key_drivers") or [])),
                mitigants=tuple(str(m) for m in (data.get("mitigants") or [])),
                escalated=bool(data.get("escalated")),
                escalation_note=str(data.get("escalation_note") or ""),
                model_ok=bool(data.get("model_ok", True)),
                model_error=str(data.get("model_error") or ""),
            )
        except (TypeError, ValueError):
            return None


def compose(
    factors: Sequence[RiskFactor],
    *,
    escalation_floor: float = 0.0,
    escalation_trigger: float = 70.0,
    escalation_share: float = 0.75,
) -> Tuple[float, bool]:
    """Combines weighted factors, lifting the result for categorical findings.

    Pure arithmetic with no model in the loop, which is what makes it testable
    exhaustively. Returns the composite and whether escalation applied.
    """
    composite = sum(factor.contribution for factor in factors)

    escalated = False
    if escalation_floor >= escalation_trigger:
        lifted = escalation_share * escalation_floor
        if lifted > composite:
            composite, escalated = lifted, True

    return max(0.0, min(100.0, composite)), escalated
