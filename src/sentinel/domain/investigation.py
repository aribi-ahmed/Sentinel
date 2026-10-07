"""The Investigation aggregate and the human decision that closes it.

An investigation is the unit of work the whole platform is organised around: it
owns the evidence gathered about one entity, the assessment derived from it, and
the human verdict that releases or rejects the result.

Modelling the human decision as a first-class object rather than a boolean
column matters — §11 wants the audit trail complete, and "who decided what, when,
and did they change anything" is the part of the trail that carries legal weight.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from sentinel.domain.analysis import RiskAssessment, RiskBand
from sentinel.domain.evidence import EvidencePool, new_id, utc_now


class InvestigationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {InvestigationStatus.COMPLETED, InvestigationStatus.REJECTED, InvestigationStatus.FAILED}


class Verdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class HumanDecision:
    """A reviewer's verdict at the approval gate, with attribution."""

    verdict: Verdict
    decided_at: datetime = field(default_factory=utc_now)
    reviewer: str = "compliance officer"
    note: str = ""
    # Set when the reviewer amended the draft rather than accepting it as-is.
    amended: bool = False

    @property
    def approved(self) -> bool:
        return self.verdict is Verdict.APPROVED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "approved": self.approved,
            "reviewer": self.reviewer,
            "decided_at": self.decided_at.isoformat(),
            "note": self.note,
            "amended": self.amended,
        }


@dataclass(frozen=True, slots=True)
class Subject:
    """The entity under investigation."""

    name: str
    ticker: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("An investigation needs a named subject.")

    @property
    def label(self) -> str:
        return f"{self.name} ({self.ticker})" if self.ticker else self.name


@dataclass(frozen=True, slots=True)
class Investigation:
    """One investigation, from request through to released report."""

    subject: Subject
    id: str = field(default_factory=lambda: new_id("inv"))
    status: InvestigationStatus = InvestigationStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    evidence: EvidencePool = field(default_factory=EvidencePool)
    assessment: Optional[RiskAssessment] = None
    decision: Optional[HumanDecision] = None
    report_markdown: str = ""

    @property
    def band(self) -> Optional[RiskBand]:
        return self.assessment.band if self.assessment else None

    @property
    def awaiting_review(self) -> bool:
        return self.assessment is not None and self.decision is None

    @property
    def has_report(self) -> bool:
        return bool(self.report_markdown.strip())

    def with_assessment(self, assessment: RiskAssessment) -> "Investigation":
        """Records the verdict and moves the investigation to the human gate."""
        return replace(
            self,
            assessment=assessment,
            status=InvestigationStatus.AWAITING_REVIEW,
            updated_at=utc_now(),
        )

    def with_decision(self, decision: HumanDecision, report_markdown: str = "") -> "Investigation":
        """Closes the investigation on the reviewer's verdict."""
        return replace(
            self,
            decision=decision,
            report_markdown=report_markdown or self.report_markdown,
            status=InvestigationStatus.COMPLETED if decision.approved else InvestigationStatus.REJECTED,
            updated_at=utc_now(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject_name": self.subject.name,
            "ticker": self.subject.ticker,
            "status": self.status.value,
            "risk_level": self.band.value if self.band else "UNKNOWN",
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "evidence_count": len(self.evidence),
            "risk_assessment": self.assessment.to_dict(self.evidence) if self.assessment else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "has_report": self.has_report,
        }
