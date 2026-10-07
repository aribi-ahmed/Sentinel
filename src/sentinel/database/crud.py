import uuid
from typing import Optional, List
from sqlalchemy.orm import Session
from sentinel.database.models import Investigation, InvestigationStatus


def create_investigation(subject_name: str, ticker: Optional[str], db: Session) -> Investigation:
    """Creates a new investigation record in the database with status RUNNING."""
    db_record = Investigation(
        subject_name=subject_name,
        ticker=ticker,
        status=InvestigationStatus.RUNNING
    )
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record


def fetch_investigation(investigation_id: uuid.UUID, db: Session) -> Optional[Investigation]:
    """Retrieves a single investigation record by UUID."""
    return db.query(Investigation).filter(Investigation.id == investigation_id).first()


def fetch_all_investigations(db: Session) -> List[Investigation]:
    """Fetches all investigation records ordered by creation date (newest first)."""
    return db.query(Investigation).order_by(Investigation.created_at.desc()).all()


def finalize_investigation(
    record_id: uuid.UUID,
    risk_level: str,
    human_approved: bool,
    supervisor_reasoning: str,
    final_report: str,
    db: Session
) -> Optional[Investigation]:
    """Updates an investigation record with the final human approval verdict and executive report."""
    db_record = fetch_investigation(record_id, db)
    if db_record:
        db_record.status = InvestigationStatus.COMPLETED if human_approved else InvestigationStatus.FAILED
        db_record.risk_level = risk_level
        db_record.human_approved = human_approved
        db_record.supervisor_reasoning = supervisor_reasoning
        db_record.final_report = final_report
        db.commit()
        db.refresh(db_record)
    return db_record