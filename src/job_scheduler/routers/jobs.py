"""Job router — POST /jobs, GET /jobs/{id}, GET /jobs/{id}/history, POST /jobs/{id}/cancel."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from job_scheduler.database import get_session
from job_scheduler.schemas.job import JobCreate, JobResponse, JobStatusResponse, CancelResponse
from job_scheduler.schemas.event import EventResponse, HistoryResponse
from job_scheduler.services import job_service

router = APIRouter(tags=["jobs"])


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreate, session: AsyncSession = Depends(get_session)):
    # Validate scheduled_at is not in the past
    if body.scheduled_at is not None:
        sa = body.scheduled_at
        if sa.tzinfo is None:
            sa = sa.replace(tzinfo=timezone.utc)
        if sa <= datetime.now(timezone.utc):
            raise HTTPException(status_code=422, detail="scheduled_at must be in the future")

    try:
        job, is_new = await job_service.create_job(session, body)
    except ValueError:
        raise HTTPException(status_code=404, detail="task not found") from None

    resp = JobResponse.model_validate(job)
    if is_new:
        return resp
    else:
        # Idempotent duplicate — return 200
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=200, content=resp.model_dump(mode="json"))


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await job_service.get_job(session, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    job, task = result
    return JobStatusResponse(
        job_id=job.job_id,
        task_id=job.task_id,
        task_name=task.name,
        status=job.status.value if hasattr(job.status, "value") else job.status,
        scheduled_at=job.scheduled_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        params=job.params or {},
        retry_count=job.retry_count,
        max_retries=task.max_retries,
        next_retry_at=job.next_retry_at,
        created_at=job.created_at,
    )


@router.get("/jobs/{job_id}/history", response_model=HistoryResponse)
async def get_history(
    job_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    result = await job_service.get_history(session, job_id, offset=offset, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    events, total = result
    return HistoryResponse(
        job_id=job_id,
        events=[EventResponse.model_validate(e) for e in events],
        offset=offset,
        limit=limit,
        total=total,
    )


@router.post("/jobs/{job_id}/cancel", response_model=CancelResponse)
async def cancel_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    job, error = await job_service.cancel_job(session, job_id)
    if error == "not_found":
        raise HTTPException(status_code=404, detail="job not found")
    if error == "conflict":
        raise HTTPException(status_code=409, detail="job is not in PENDING state")
    return CancelResponse(
        job_id=job.job_id, status=job.status.value if hasattr(job.status, "value") else job.status
    )
