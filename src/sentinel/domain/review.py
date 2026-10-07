"""Review records produced by the critic.

A challenge is recorded only when something checkable is wrong: an unresolvable
citation, a factor carrying no evidence, a dimension scored low because the tool
behind it never ran. Severity separates defects that make a verdict wrong
(blocking) from those that make it weaker than it claims (material).

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from sentinel.domain.evidence import new_id, utc_now


class ChallengeKind(str, Enum):
    """What sort of defect was found. Each maps to one deterministic check."""

    DANGLING_CITATION = "dangling_citation"      # cites evidence that does not exist
    UNGROUNDED_FACTOR = "ungrounded_factor"      # scored on judgement alone
    BAND_MISMATCH = "band_mismatch"              # the band does not follow from the score
    UNVERIFIED_SCOPE = "unverified_scope"        # a tool never ran; absence read as safety
    THIN_EVIDENCE = "thin_evidence"              # a serious verdict on weak provenance
    OVERCONFIDENT = "overconfident"              # confidence outruns the evidence base
    UNSUPPORTED_DRIVER = "unsupported_driver"    # a stated driver no evidence supports


class ChallengeSeverity(str, Enum):
    """How much the defect costs the verdict.

    `BLOCKING` is reserved for defects that make the verdict *wrong* rather than
    merely weak — a citation that resolves to nothing, a band that contradicts
    its own score, a clean sanctions dimension produced by a screening that never
    ran. Everything a reviewer would want to know but that does not invalidate
    the number is `MATERIAL`.
    """

    BLOCKING = "blocking"
    MATERIAL = "material"
    ADVISORY = "advisory"

    @property
    def rank(self) -> int:
        return {"advisory": 0, "material": 1, "blocking": 2}[self.value]


@dataclass(frozen=True, slots=True)
class Challenge:
    """One specific objection, with what would answer it."""

    kind: ChallengeKind
    severity: ChallengeSeverity
    subject: str          # the factor id, driver text, or tool name at issue
    detail: str           # what is wrong, stated as an observation
    remedy: str = ""      # what would resolve it
    id: str = field(default_factory=lambda: new_id("ch"))

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError(
                f"Challenge {self.kind.value!r} carries no detail. An objection a "
                "reviewer cannot act on is worse than none."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "subject": self.subject,
            "detail": self.detail,
            "remedy": self.remedy,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Challenge"]:
        if not isinstance(data, dict) or not str(data.get("detail") or "").strip():
            return None
        try:
            kind = ChallengeKind(str(data.get("kind")))
            severity = ChallengeSeverity(str(data.get("severity")))
        except ValueError:
            return None
        return cls(
            kind=kind,
            severity=severity,
            subject=str(data.get("subject") or ""),
            detail=str(data["detail"]),
            remedy=str(data.get("remedy") or ""),
            id=str(data.get("id") or new_id("ch")),
        )


class ReviewVerdict(str, Enum):
    """The critic's disposition toward the assessment it read."""

    ENDORSED = "endorsed"      # nothing material found
    QUALIFIED = "qualified"    # stands, but the reader must know what is weak
    REJECTED = "rejected"      # must not be released in this form

    @property
    def blocks_release(self) -> bool:
        return self is ReviewVerdict.REJECTED


@dataclass(frozen=True, slots=True)
class CriticReview:
    """The outcome of one review pass over one assessment."""

    verdict: ReviewVerdict
    challenges: Tuple[Challenge, ...] = ()
    # Applied to the supervisor's confidence. Never positive: a second reading
    # can find reasons to trust a verdict less, never reasons to trust it more.
    confidence_penalty: float = 0.0
    revision: int = 0
    model_ok: bool = True
    model_error: str = ""
    id: str = field(default_factory=lambda: new_id("rev"))
    reviewed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.confidence_penalty < 0:
            raise ValueError(
                "confidence_penalty is a deduction and must not be negative; the "
                "critic may lower confidence, never raise it."
            )

    @property
    def blocking(self) -> Tuple[Challenge, ...]:
        return tuple(c for c in self.challenges if c.severity is ChallengeSeverity.BLOCKING)

    @property
    def material(self) -> Tuple[Challenge, ...]:
        return tuple(c for c in self.challenges if c.severity is ChallengeSeverity.MATERIAL)

    @property
    def is_clean(self) -> bool:
        return not self.challenges

    def by_kind(self, kind: ChallengeKind) -> Tuple[Challenge, ...]:
        return tuple(c for c in self.challenges if c.kind is kind)

    def adjusted_confidence(self, confidence: float) -> float:
        """The supervisor's confidence after the critic's deduction."""
        return round(max(0.0, min(1.0, confidence - self.confidence_penalty)), 2)

    def summary(self) -> str:
        """One line for the audit log and the console."""
        if self.is_clean:
            return "Critic endorsed the assessment: no unsupported factors, every citation resolves."

        counts = []
        if self.blocking:
            counts.append(f"{len(self.blocking)} blocking")
        if self.material:
            counts.append(f"{len(self.material)} material")
        advisory = len(self.challenges) - len(self.blocking) - len(self.material)
        if advisory:
            counts.append(f"{advisory} advisory")

        kinds = ", ".join(sorted({c.kind.value for c in self.challenges}))
        return f"Critic {self.verdict.value} the assessment: {', '.join(counts)} ({kinds})."

    def briefing(self) -> str:
        """The critique as text, for feeding back to the supervisor on revision."""
        if self.is_clean:
            return ""
        lines = [
            f"- [{c.severity.value}] {c.detail}" + (f" Remedy: {c.remedy}" if c.remedy else "")
            for c in self.challenges
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "verdict": self.verdict.value,
            "summary": self.summary(),
            "challenges": [c.to_dict() for c in self.challenges],
            "blocking": len(self.blocking),
            "material": len(self.material),
            "confidence_penalty": round(self.confidence_penalty, 2),
            "revision": self.revision,
            "model_ok": self.model_ok,
            "model_error": self.model_error,
            "reviewed_at": self.reviewed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["CriticReview"]:
        """Rebuilds a review read back from the checkpoint. Never raises."""
        if not isinstance(data, dict) or not data.get("verdict"):
            return None
        try:
            verdict = ReviewVerdict(str(data["verdict"]))
        except ValueError:
            return None
        try:
            reviewed_at = datetime.fromisoformat(str(data.get("reviewed_at", "")))
        except ValueError:
            reviewed_at = utc_now()

        rebuilt = (Challenge.from_dict(row) for row in (data.get("challenges") or []))
        return cls(
            verdict=verdict,
            challenges=tuple(c for c in rebuilt if c is not None),
            confidence_penalty=max(0.0, float(data.get("confidence_penalty") or 0.0)),
            revision=int(data.get("revision") or 0),
            model_ok=bool(data.get("model_ok", True)),
            model_error=str(data.get("model_error") or ""),
            id=str(data.get("id") or new_id("rev")),
            reviewed_at=reviewed_at,
        )


def decide_verdict(challenges: Tuple[Challenge, ...]) -> ReviewVerdict:
    """Maps a set of challenges onto a disposition.

    Kept as a free function so the rule is one readable line rather than being
    spread across the service that builds the challenges.
    """
    if any(c.severity is ChallengeSeverity.BLOCKING for c in challenges):
        return ReviewVerdict.REJECTED
    if any(c.severity is ChallengeSeverity.MATERIAL for c in challenges):
        return ReviewVerdict.QUALIFIED
    return ReviewVerdict.ENDORSED
