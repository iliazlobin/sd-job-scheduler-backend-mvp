"""Pydantic schemas for execution event endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EventResponse(BaseModel):
    """A single execution event in a job's history timeline."""

    event_id: int
    job_id: uuid.UUID
    event_type: str
    timestamp: datetime
    payload: dict[str, Any]

    model_config = {"from_attributes": True}


class HistoryResponse(BaseModel):
    """Response body for GET /jobs/{job_id}/history — paginated event list."""

    job_id: uuid.UUID
    events: list[EventResponse]
    total: int
    offset: int
    limit: int
