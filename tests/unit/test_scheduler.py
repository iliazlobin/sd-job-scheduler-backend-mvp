"""Unit tests for scheduler (white-box, import the app)."""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from job_scheduler.schemas.task import TaskCreate
from job_scheduler.services.task_service import create_task
from job_scheduler.services.scheduler import Scheduler


@pytest.mark.asyncio
async def test_scheduler_instantiation():
    """Scheduler should initialize with correct default state."""
    s = Scheduler()
    assert s._running is False
    assert s._task is None


@pytest.mark.asyncio
async def test_scheduler_claims_pending_job(session):
    """Scheduler should claim a PENDING job whose scheduled_at has passed."""
    # Create a task
    task = await create_task(session, TaskCreate(name=f"sched-claim-{uuid.uuid4().hex[:8]}"))

    # Insert a job directly via SQL with scheduled_at in the past
    # (bypasses Pydantic validation that requires future scheduled_at)
    job_id = uuid.uuid4()
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    await session.execute(
        text("""
            INSERT INTO jobs (job_id, task_id, status, scheduled_at, params, retry_count)
            VALUES (:job_id, :task_id, 'PENDING', :scheduled_at, '{}', 0)
        """),
        {"job_id": job_id, "task_id": task.task_id, "scheduled_at": past_time},
    )
    await session.commit()

    # Verify job is PENDING
    result = await session.execute(
        text("SELECT status FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    status = result.scalar()
    assert status == "PENDING"

    # Run scheduler tick
    scheduler = Scheduler()
    await scheduler._claim_and_execute(session)

    # Verify job transitioned to SUCCESS
    result = await session.execute(
        text("SELECT status FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    final_status = result.scalar()
    assert final_status == "SUCCESS"


@pytest.mark.asyncio
async def test_scheduler_transitions_to_success_and_emits_events(session):
    """Scheduler should transition job to SUCCESS and emit CLAIMED, STARTED, COMPLETED events."""
    task = await create_task(session, TaskCreate(name=f"sched-success-{uuid.uuid4().hex[:8]}"))

    # Insert job directly with past scheduled_at
    job_id = uuid.uuid4()
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    await session.execute(
        text("""
            INSERT INTO jobs (job_id, task_id, status, scheduled_at, params, retry_count)
            VALUES (:job_id, :task_id, 'PENDING', :scheduled_at, '{}', 0)
        """),
        {"job_id": job_id, "task_id": task.task_id, "scheduled_at": past_time},
    )
    await session.commit()

    # Run scheduler tick
    scheduler = Scheduler()
    await scheduler._claim_and_execute(session)

    # Verify final status
    result = await session.execute(
        text("SELECT status, completed_at FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    row = result.first()
    assert row[0] == "SUCCESS"
    assert row[1] is not None  # completed_at should be set

    # Verify events were emitted
    result = await session.execute(
        text("SELECT event_type FROM execution_events WHERE job_id = :id ORDER BY event_id"),
        {"id": job_id},
    )
    events = [row[0] for row in result.fetchall()]

    # Should have: CLAIMED, STARTED, COMPLETED (no ENQUEUED since we inserted directly)
    assert "CLAIMED" in events
    assert "STARTED" in events
    assert "COMPLETED" in events


@pytest.mark.asyncio
async def test_scheduler_retry_loop_with_fail(session):
    """Scheduler should retry a failed job if retries remain."""
    # Create task with max_retries=2
    task = await create_task(
        session,
        TaskCreate(name=f"sched-retry-{uuid.uuid4().hex[:8]}", max_retries=2, timeout_sec=60),
    )

    # Insert job with fail=true param
    job_id = uuid.uuid4()
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    await session.execute(
        text("""
            INSERT INTO jobs (job_id, task_id, status, scheduled_at, params, retry_count)
            VALUES (:job_id, :task_id, 'PENDING', :scheduled_at, :params, 0)
        """),
        {
            "job_id": job_id,
            "task_id": task.task_id,
            "scheduled_at": past_time,
            "params": '{"fail": true, "error_message": "test failure"}',
        },
    )
    await session.commit()

    scheduler = Scheduler()

    # First execution - should fail and retry
    await scheduler._claim_and_execute(session)

    result = await session.execute(
        text("SELECT status, retry_count, next_retry_at FROM jobs WHERE job_id = :id"),
        {"id": job_id},
    )
    row = result.first()
    assert row[0] == "PENDING"  # Should be back to PENDING for retry
    assert row[1] == 1  # retry_count incremented
    assert row[2] is not None  # next_retry_at should be set

    # Verify FAILED and RETRYING events
    result = await session.execute(
        text("SELECT event_type FROM execution_events WHERE job_id = :id ORDER BY event_id"),
        {"id": job_id},
    )
    events = [row[0] for row in result.fetchall()]
    assert "FAILED" in events
    assert "RETRYING" in events


@pytest.mark.asyncio
async def test_scheduler_exponential_backoff_timing(session):
    """Scheduler should apply exponential backoff: 2^retry_count seconds."""
    task = await create_task(
        session,
        TaskCreate(name=f"sched-backoff-{uuid.uuid4().hex[:8]}", max_retries=3, timeout_sec=60),
    )

    # Insert job with fail=true
    job_id = uuid.uuid4()
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    await session.execute(
        text("""
            INSERT INTO jobs (job_id, task_id, status, scheduled_at, params, retry_count)
            VALUES (:job_id, :task_id, 'PENDING', :scheduled_at, '{"fail": true}', 0)
        """),
        {"job_id": job_id, "task_id": task.task_id, "scheduled_at": past_time},
    )
    await session.commit()

    scheduler = Scheduler()

    # First failure - retry_count=0, backoff should be 2^0 = 1 second
    before = datetime.now(timezone.utc)
    await scheduler._claim_and_execute(session)

    result = await session.execute(
        text("SELECT retry_count, next_retry_at FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    row = result.first()
    assert row[0] == 1
    next_retry = row[1]

    # next_retry_at should be approximately 1 second after before
    expected_min = before + timedelta(seconds=0.9)
    expected_max = before + timedelta(seconds=2)
    assert expected_min <= next_retry <= expected_max

    # Manually set next_retry_at to past to allow immediate retry
    await session.execute(
        text("UPDATE jobs SET next_retry_at = :past WHERE job_id = :id"),
        {"past": datetime.now(timezone.utc) - timedelta(seconds=1), "id": job_id},
    )
    await session.commit()

    # Second failure - retry_count=1, backoff should be 2^1 = 2 seconds
    before = datetime.now(timezone.utc)
    await scheduler._claim_and_execute(session)

    result = await session.execute(
        text("SELECT retry_count, next_retry_at FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    row = result.first()
    assert row[0] == 2
    next_retry = row[1]

    # next_retry_at should be approximately 2 seconds after before
    expected_min = before + timedelta(seconds=1.9)
    expected_max = before + timedelta(seconds=3)
    assert expected_min <= next_retry <= expected_max


@pytest.mark.asyncio
async def test_scheduler_terminal_failure_after_retries_exhausted(session):
    """Scheduler should mark job as FAILED when retries are exhausted."""
    task = await create_task(
        session,
        TaskCreate(
            name=f"sched-terminal-{uuid.uuid4().hex[:8]}",
            max_retries=1,  # Only 1 retry allowed
            timeout_sec=60,
        ),
    )

    # Insert job with fail=true
    job_id = uuid.uuid4()
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    await session.execute(
        text("""
            INSERT INTO jobs (job_id, task_id, status, scheduled_at, params, retry_count)
            VALUES (:job_id, :task_id, 'PENDING', :scheduled_at, '{"fail": true}', 0)
        """),
        {"job_id": job_id, "task_id": task.task_id, "scheduled_at": past_time},
    )
    await session.commit()

    scheduler = Scheduler()

    # First execution - should fail and retry (retry_count 0 -> 1)
    await scheduler._claim_and_execute(session)

    result = await session.execute(
        text("SELECT status, retry_count FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    row = result.first()
    assert row[0] == "PENDING"
    assert row[1] == 1

    # Set next_retry_at to past to allow immediate retry
    await session.execute(
        text("UPDATE jobs SET next_retry_at = :past WHERE job_id = :id"),
        {"past": datetime.now(timezone.utc) - timedelta(seconds=1), "id": job_id},
    )
    await session.commit()

    # Second execution - retry_count=1, max_retries=1, should fail terminally
    await scheduler._claim_and_execute(session)

    result = await session.execute(
        text("SELECT status FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    final_status = result.scalar()
    assert final_status == "FAILED"

    # Verify multiple FAILED events (one per attempt)
    result = await session.execute(
        text("SELECT event_type FROM execution_events WHERE job_id = :id ORDER BY event_id"),
        {"id": job_id},
    )
    events = [row[0] for row in result.fetchall()]
    assert events.count("FAILED") == 2  # One for each attempt


@pytest.mark.asyncio
async def test_scheduler_does_not_claim_future_jobs(session):
    """Scheduler should not claim jobs scheduled in the future."""
    task = await create_task(session, TaskCreate(name=f"sched-future-{uuid.uuid4().hex[:8]}"))

    # Insert job with future scheduled_at
    job_id = uuid.uuid4()
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)
    await session.execute(
        text("""
            INSERT INTO jobs (job_id, task_id, status, scheduled_at, params, retry_count)
            VALUES (:job_id, :task_id, 'PENDING', :scheduled_at, '{}', 0)
        """),
        {"job_id": job_id, "task_id": task.task_id, "scheduled_at": future_time},
    )
    await session.commit()

    scheduler = Scheduler()
    await scheduler._claim_and_execute(session)

    # Job should still be PENDING
    result = await session.execute(
        text("SELECT status FROM jobs WHERE job_id = :id"), {"id": job_id}
    )
    status = result.scalar()
    assert status == "PENDING"
