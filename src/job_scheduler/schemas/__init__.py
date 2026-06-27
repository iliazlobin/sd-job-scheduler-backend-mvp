"""Pydantic schemas for the Job Scheduler API.

Re-exports all request/response models for convenient imports:
    from job_scheduler.schemas import TaskCreate, JobResponse, ...
"""

from job_scheduler.schemas.task import TaskCreate, TaskResponse
from job_scheduler.schemas.job import (
    JobCreate,
    JobResponse,
    JobStatusResponse,
    CancelResponse,
)
from job_scheduler.schemas.event import EventResponse, HistoryResponse

__all__ = [
    # Task
    "TaskCreate",
    "TaskResponse",
    # Job
    "JobCreate",
    "JobResponse",
    "JobStatusResponse",
    "CancelResponse",
    # Event / History
    "EventResponse",
    "HistoryResponse",
]
