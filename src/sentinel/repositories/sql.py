"""SQLAlchemy-backed investigation repository.

This is the only place in the codebase that knows both the domain model and the
database schema, and its whole job is translation between them. Everything above
it — services, orchestration, the API — sees domain objects.

The mapping is deliberately explicit rather than an ORM-mapped domain class:
the domain must not import SQLAlchemy (§10.6), so the two models stay separate
and this module pays the small cost of converting between them.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from sentinel.database.models import Investigation as InvestigationRow
from sentinel.database.models import InvestigationStatus as RowStatus
from sentinel.domain.analysis import RiskAssessment, RiskFactor, Severity  # noqa: F401
from sentinel.domain.evidence import EvidencePool
from sentinel.domain.investigation import (
    HumanDecision,
    Investigation,
    InvestigationStatus,
    Subject,
    Verdict,
)

logger = logging.getLogger(__name__)

# Domain statuses are richer than the original column vocabulary, so they are
# mapped rather than assumed equal. AWAITING_REVIEW has no stored equivalent —
# the row stays RUNNING until a human decides — which is why it maps down.
_TO_ROW = {
    InvestigationStatus.PENDING: RowStatus.PENDING,
    InvestigationStatus.RUNNING: RowStatus.RUNNING,
    InvestigationStatus.AWAITING_REVIEW: RowStatus.RUNNING,
    InvestigationStatus.COMPLETED: RowStatus.COMPLETED,
    InvestigationStatus.REJECTED: RowStatus.FAILED,
    InvestigationStatus.FAILED: RowStatus.FAILED,
}


def _row_status(row: InvestigationRow) -> InvestigationStatus:
    """Recovers the domain status, using the decision to disambiguate."""
    raw = row.status.value if hasattr(row.status, "value") else str(row.status)
    if raw == RowStatus.COMPLETED.value:
        return InvestigationStatus.COMPLETED
    if raw == RowStatus.FAILED.value:
        return InvestigationStatus.REJECTED if row.human_approved is False else InvestigationStatus.FAILED
    if raw == RowStatus.RUNNING.value:
        # A running row that already carries reasoning is parked at the gate.
        return InvestigationStatus.AWAITING_REVIEW if row.supervisor_reasoning else InvestigationStatus.RUNNING
    return InvestigationStatus.PENDING


def _decode(blob: Optional[str]) -> Dict[str, Any]:
    if not blob:
        return {}
    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SqlInvestigationRepository:
    """Maps `Investigation` aggregates onto the `investigations` table.

    The session is injected rather than created here: request scope belongs to
    the caller, which keeps the repository usable inside a FastAPI dependency,
    a background worker, or a migration script without changing.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- translation ------------------------------------------------------

    def _to_domain(self, row: InvestigationRow) -> Investigation:
        assessment_payload = _decode(getattr(row, "assessment_json", None))
        evidence_payload = assessment_payload.get("evidence") or []

        decision: Optional[HumanDecision] = None
        if row.human_approved is not None:
            decision = HumanDecision(
                verdict=Verdict.APPROVED if row.human_approved else Verdict.REJECTED,
                decided_at=row.updated_at or row.created_at,
            )

        return Investigation(
            id=str(row.id),
            subject=Subject(name=row.subject_name, ticker=row.ticker or ""),
            status=_row_status(row),
            created_at=row.created_at,
            updated_at=row.updated_at or row.created_at,
            evidence=EvidencePool.from_list(evidence_payload),
            assessment=None,  # rehydrated by the service layer when needed
            decision=decision,
            report_markdown=row.final_report or "",
        )

    def _apply(self, row: InvestigationRow, investigation: Investigation) -> InvestigationRow:
        row.subject_name = investigation.subject.name
        row.ticker = investigation.subject.ticker
        row.status = _TO_ROW[investigation.status]
        row.final_report = investigation.report_markdown

        if investigation.assessment is not None:
            row.risk_level = investigation.assessment.band.value
            row.supervisor_reasoning = investigation.assessment.summary

        if investigation.decision is not None:
            row.human_approved = investigation.decision.approved

        return row

    # -- InvestigationRepository ------------------------------------------

    def add(self, investigation: Investigation) -> Investigation:
        row = InvestigationRow(subject_name=investigation.subject.name)
        try:
            row.id = uuid.UUID(investigation.id)
        except (ValueError, AttributeError):
            pass  # domain-generated ids are not UUIDs; let the column default stand
        self._apply(row, investigation)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return self._to_domain(row)

    def get(self, investigation_id: str) -> Optional[Investigation]:
        row = self._row(investigation_id)
        return self._to_domain(row) if row else None

    def save(self, investigation: Investigation) -> Investigation:
        row = self._row(investigation.id)
        if row is None:
            raise KeyError(f"Investigation {investigation.id} does not exist; use add().")
        self._apply(row, investigation)
        self._session.commit()
        self._session.refresh(row)
        return self._to_domain(row)

    def list_recent(self, limit: int = 200) -> List[Investigation]:
        rows = (
            self._session.query(InvestigationRow)
            .order_by(InvestigationRow.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._to_domain(row) for row in rows]

    def count(self) -> int:
        return self._session.query(InvestigationRow).count()

    # -- internals --------------------------------------------------------

    def _row(self, investigation_id: str) -> Optional[InvestigationRow]:
        try:
            key = uuid.UUID(str(investigation_id))
        except (ValueError, TypeError):
            return None
        return (
            self._session.query(InvestigationRow)
            .filter(InvestigationRow.id == key)
            .first()
        )
