"""Task model — defines a reusable job template with retry/timeout policy."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_scheduler.database import Base


class Task(Base):
    """A task definition: name + retry/timeout policy.

    Jobs are instances of a task. The task itself is immutable once created.
    """

    __tablename__ = "tasks"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="3",
    )
    timeout_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="3600",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="NOW()",
    )

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(  # noqa: F821
        back_populates="task",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Task task_id={self.task_id} name={self.name!r}>"
