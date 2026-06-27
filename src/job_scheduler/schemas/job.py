"""Pydantic schemas for Job endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class JobCreate(BaseModel):
    """Request body for POST /jobs."""

    task_id: uuid.UUID = Field(..., description="UUID of the parent task")
    scheduled_at: Optional[datetime] = Field(
        default=None,
        description="Optional future execution time (ISO 8601, UTC). Must be >= now.",
    )
    params: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Arbitrary JSON parameters for the job",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Client-generated dedup key; second call with same key returns existing job",
    )

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_be_future(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Reject scheduled_at in the past."""
        if v is not None:
            now = datetime.now(timezone.utc)
            # Ensure the value is timezone-aware for comparison
            if v.tzinfo is None:
                raise ValueError("scheduled_at must include timezone information")
            if v < now:
                raise ValueError("scheduled_at must be >= current time (UTC)")
        return v


class JobResponse(BaseModel):
    """Response body for POST /jobs (201/200)."""

    job_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    scheduled_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    params: dict[str, Any]
    idempotency_key: Optional[str] = None
    retry_count: int = 0
    next_retry_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobStatusResponse(BaseModel):
    """Response body for GET /jobs/{job_id} — includes task_name and max_retries."""

    job_id: uuid.UUID
    task_id: uuid.UUID
    task_name: str
    status: str
    scheduled_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    params: dict[str, Any]
    retry_count: int = 0
    max_retries: int
    next_retry_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CancelResponse(BaseModel):
    """Response body for POST /jobs/{job_id}/cancel."""

    job_id: uuid.UUID
    status: str
