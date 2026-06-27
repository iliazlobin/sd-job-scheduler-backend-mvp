"""initial schema — tasks, jobs, execution_events + enums + indexes

Revision ID: 001_initial
Revises: None
Create Date: 2026-06-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- tasks table --
    op.create_table(
        "tasks",
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("timeout_sec", sa.Integer(), server_default="3600", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )

    # -- jobs table --
    op.create_table(
        "jobs",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "CLAIMED", "RUNNING", "SUCCESS", "FAILED", "CANCELLED", name="job_status"
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("params", JSONB(), server_default="{}", nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key"),
    )
    # Individual indexes for general lookups
    op.create_index("ix_jobs_task_id", "jobs", ["task_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_scheduled_at", "jobs", ["scheduled_at"])
    # Partial composite index for the scheduler's main poll query
    op.create_index(
        "idx_jobs_status_scheduled",
        "jobs",
        ["status", "scheduled_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # -- execution_events table --
    op.create_table(
        "execution_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "ENQUEUED",
                "CLAIMED",
                "STARTED",
                "COMPLETED",
                "FAILED",
                "RETRYING",
                "CANCELLED",
                name="event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False
        ),
        sa.Column("payload", JSONB(), server_default="{}", nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
    )
    # Composite index for history queries (events by job, chronological)
    op.create_index("idx_events_job_time", "execution_events", ["job_id", "timestamp"])
    op.create_index("ix_events_job_id", "execution_events", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_events_job_id", table_name="execution_events")
    op.drop_index("idx_events_job_time", table_name="execution_events")
    op.drop_table("execution_events")

    op.drop_index("idx_jobs_status_scheduled", table_name="jobs")
    op.drop_index("ix_jobs_scheduled_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_task_id", table_name="jobs")
    op.drop_table("jobs")

    op.drop_table("tasks")

    op.execute("DROP TYPE IF EXISTS event_type")
    op.execute("DROP TYPE IF EXISTS job_status")
