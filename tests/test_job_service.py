"""White-box tests for job service."""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from job_scheduler.schemas.task import TaskCreate
from job_scheduler.schemas.job import JobCreate


@pytest.mark.asyncio
async def test_create_job(session):
    from datetime import datetime, timezone, timedelta
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job

    task = await create_task(session, TaskCreate(name=f"job-test-task-{uuid.uuid4().hex[:8]}"))
    # Schedule far in the future to keep it PENDING (scheduler won't pick it up)
    job, is_new = await create_job(
        session,
        JobCreate(
            task_id=task.task_id, scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
    )
    assert is_new is True
    assert job.task_id == task.task_id
    assert job.status.value == "PENDING"


@pytest.mark.asyncio
async def test_create_job_idempotency(session):
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job

    task = await create_task(session, TaskCreate(name=f"idemp-test-task-{uuid.uuid4().hex[:8]}"))
    idem_key = f"test-key-{uuid.uuid4().hex[:8]}"
    job1, is_new1 = await create_job(
        session, JobCreate(task_id=task.task_id, idempotency_key=idem_key)
    )
    assert is_new1 is True

    job2, is_new2 = await create_job(
        session, JobCreate(task_id=task.task_id, idempotency_key=idem_key)
    )
    assert is_new2 is False
    assert job2.job_id == job1.job_id


@pytest.mark.asyncio
async def test_create_job_unknown_task(session):
    from job_scheduler.services.job_service import create_job

    with pytest.raises(ValueError, match="task_not_found"):
        await create_job(session, JobCreate(task_id=uuid.uuid4()))


@pytest.mark.asyncio
async def test_create_job_delayed(session):
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job

    task = await create_task(session, TaskCreate(name=f"delayed-test-{uuid.uuid4().hex[:8]}"))
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)
    job, is_new = await create_job(
        session, JobCreate(task_id=task.task_id, scheduled_at=future_time)
    )
    assert is_new is True
    assert job.scheduled_at == future_time
    assert job.status.value == "PENDING"


@pytest.mark.asyncio
async def test_get_job(session):
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job, get_job

    task = await create_task(session, TaskCreate(name=f"get-job-test-{uuid.uuid4().hex[:8]}"))
    created_job, _ = await create_job(session, JobCreate(task_id=task.task_id))

    result = await get_job(session, created_job.job_id)
    assert result is not None
    job, task_obj = result
    assert job.job_id == created_job.job_id
    assert task_obj.task_id == task.task_id


@pytest.mark.asyncio
async def test_get_job_not_found(session):
    from job_scheduler.services.job_service import get_job

    result = await get_job(session, uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_cancel_job(session):
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job, cancel_job

    task = await create_task(session, TaskCreate(name=f"cancel-test-{uuid.uuid4().hex[:8]}"))
    job, _ = await create_job(session, JobCreate(task_id=task.task_id))

    cancelled_job, error = await cancel_job(session, job.job_id)
    assert error is None
    assert cancelled_job.status.value == "CANCELLED"
    assert cancelled_job.completed_at is not None


@pytest.mark.asyncio
async def test_cancel_job_not_found(session):
    from job_scheduler.services.job_service import cancel_job

    result, error = await cancel_job(session, uuid.uuid4())
    assert result is None
    assert error == "not_found"


@pytest.mark.asyncio
async def test_cancel_job_conflict(session):
    from datetime import datetime, timezone, timedelta
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job, cancel_job

    task = await create_task(session, TaskCreate(name=f"conflict-test-{uuid.uuid4().hex[:8]}"))
    # Schedule far in the future to keep it PENDING
    job, _ = await create_job(
        session,
        JobCreate(
            task_id=task.task_id, scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
    )

    # Cancel once
    await cancel_job(session, job.job_id)

    # Try to cancel again - should conflict
    result, error = await cancel_job(session, job.job_id)
    assert error == "conflict"
    assert result.status.value == "CANCELLED"


@pytest.mark.asyncio
async def test_get_history(session):
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job, cancel_job, get_history

    task = await create_task(session, TaskCreate(name=f"history-test-{uuid.uuid4().hex[:8]}"))
    job, _ = await create_job(session, JobCreate(task_id=task.task_id))

    # Get history after creation (should have ENQUEUED event)
    events, total = await get_history(session, job.job_id)
    assert total == 1
    assert len(events) == 1
    assert events[0].event_type.value == "ENQUEUED"

    # Cancel the job
    await cancel_job(session, job.job_id)

    # Get history again (should have ENQUEUED + CANCELLED events)
    events, total = await get_history(session, job.job_id)
    assert total == 2
    assert events[0].event_type.value == "ENQUEUED"
    assert events[1].event_type.value == "CANCELLED"


@pytest.mark.asyncio
async def test_get_history_pagination(session):
    from job_scheduler.services.task_service import create_task
    from job_scheduler.services.job_service import create_job, get_history

    task = await create_task(session, TaskCreate(name=f"pagination-test-{uuid.uuid4().hex[:8]}"))
    job, _ = await create_job(session, JobCreate(task_id=task.task_id))

    # Test pagination - job may have multiple events if scheduler is running
    events, total = await get_history(session, job.job_id, limit=10, offset=0)
    assert total >= 1
    assert len(events) >= 1
