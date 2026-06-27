"""Pydantic schemas for Task endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class TaskCreate(BaseModel):
    """Request body for POST /tasks."""

    name: str = Field(..., min_length=1, description="Unique task name, non-empty")
    max_retries: int = Field(default=3, ge=0, description="Max retry attempts (>= 0, 0=no retries)")
    timeout_sec: int = Field(default=3600, ge=1, description="Execution timeout in seconds (>= 1)")


class TaskResponse(BaseModel):
    """Response body for POST /tasks (201)."""

    task_id: uuid.UUID
    name: str
    max_retries: int
    timeout_sec: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
