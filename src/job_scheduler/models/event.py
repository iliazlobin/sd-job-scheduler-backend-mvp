"""ExecutionEvent model — append-only audit log for job state transitions."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_scheduler.database import Base


class EventType(str, enum.Enum):
    """Types of events emitted during a job's lifecycle."""

    ENQUEUED = "ENQUEUED"
    CLAIMED = "CLAIMED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class ExecutionEvent(Base):
    """An immutable record of a job state transition.

    Every status change on a Job produces exactly one ExecutionEvent.
    The event log is the source of truth for the job's history timeline.
    """

    __tablename__ = "execution_events"

    event_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type", create_constraint=True),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="NOW()",
    )
    payload: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default="{}",
    )

    # Relationships
    job: Mapped["Job"] = relationship(  # noqa: F821
        back_populates="events",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<ExecutionEvent event_id={self.event_id} "
            f"type={self.event_type.value} job_id={self.job_id}>"
        )
