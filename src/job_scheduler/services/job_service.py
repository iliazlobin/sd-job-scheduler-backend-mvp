"""Job service — create (with idempotency), get, cancel, history."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_scheduler.models.task import Task
from job_scheduler.models.job import Job, JobStatus
from job_scheduler.models.event import ExecutionEvent, EventType
from job_scheduler.schemas.job import JobCreate


async def create_job(session: AsyncSession, data: JobCreate) -> tuple[Job, bool]:
    """Create a job. Returns (job, is_new). If idempotency_key collides, returns existing job with is_new=False."""
    # Check task exists
    task = await session.get(Task, data.task_id)
    if task is None:
        raise ValueError("task_not_found")

    scheduled_at = data.scheduled_at or datetime.now(timezone.utc)
    params = data.params or {}

    job = Job(
        task_id=data.task_id,
        scheduled_at=scheduled_at,
        params=params,
        idempotency_key=data.idempotency_key,
    )
    session.add(job)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # Idempotency key collision — fetch existing
        if data.idempotency_key:
            result = await session.execute(
                select(Job).where(Job.idempotency_key == data.idempotency_key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return existing, False
        raise

    await session.refresh(job)

    # Emit ENQUEUED event
    event = ExecutionEvent(job_id=job.job_id, event_type=EventType.ENQUEUED, payload={})
    session.add(event)
    await session.commit()
    await session.refresh(job)

    return job, True


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[Job, Task] | None:
    result = await session.execute(
        select(Job, Task).join(Task, Job.task_id == Task.task_id).where(Job.job_id == job_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return row[0], row[1]


async def cancel_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[Job, str]:
    """Cancel a PENDING job. Returns (job, error_type). error_type is None on success, 'not_found' or 'conflict'."""
    result = await session.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None, "not_found"

    if job.status != JobStatus.PENDING:
        return job, "conflict"

    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now(timezone.utc)

    event = ExecutionEvent(job_id=job.job_id, event_type=EventType.CANCELLED, payload={})
    session.add(event)
    await session.commit()
    await session.refresh(job)
    return job, None


async def get_history(
    session: AsyncSession, job_id: uuid.UUID, offset: int = 0, limit: int = 20
) -> tuple[list[ExecutionEvent], int] | None:
    """Get paginated event history for a job. Returns None if job doesn't exist."""
    # Check job exists
    job = await session.get(Job, job_id)
    if job is None:
        return None

    # Count total
    count_result = await session.execute(
        select(func.count()).select_from(ExecutionEvent).where(ExecutionEvent.job_id == job_id)
    )
    total = count_result.scalar()

    # Fetch paginated events
    result = await session.execute(
        select(ExecutionEvent)
        .where(ExecutionEvent.job_id == job_id)
        .order_by(ExecutionEvent.timestamp.asc(), ExecutionEvent.event_id.asc())
        .offset(offset)
        .limit(limit)
    )
    events = list(result.scalars().all())
    return events, total
