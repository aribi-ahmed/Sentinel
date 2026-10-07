# src/sentinel/db/models.py
import uuid
import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Enum, DateTime, Text, Boolean, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column



# Base class for all models
class Base(DeclarativeBase):
    pass


# Define the possible states of an investigation
class InvestigationStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"        # Waiting for human approval checkpoint
    COMPLETED = "completed"  # Officer Approved
    FAILED = "failed"        # Officer Rejected or Error


class Investigation(Base):
    __tablename__ = "investigations"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Target Meta
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Lifecycle Status
    status: Mapped[InvestigationStatus] = mapped_column(
        Enum(InvestigationStatus), 
        default=InvestigationStatus.PENDING
    )
    
    # Risk Assessment Findings
    risk_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g., "LOW", "ELEVATED"
    human_approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    supervisor_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_report: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc), 
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<Investigation(id='{self.id}', subject='{self.subject_name}', status='{self.status.value}')>"