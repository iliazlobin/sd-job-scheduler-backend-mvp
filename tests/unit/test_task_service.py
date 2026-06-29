"""Unit tests for task service (white-box, import the app)."""

import pytest
import uuid
from job_scheduler.schemas.task import TaskCreate


@pytest.mark.asyncio
async def test_create_task(session):
    from job_scheduler.services.task_service import create_task

    name = f"test-task-{uuid.uuid4().hex[:8]}"
    data = TaskCreate(name=name, max_retries=2, timeout_sec=60)
    task = await create_task(session, data)
    assert task.name == name
    assert task.max_retries == 2
    assert task.timeout_sec == 60
    assert task.task_id is not None


@pytest.mark.asyncio
async def test_create_task_duplicate_name(session):
    """Creating tasks with the same name should succeed (no unique constraint)."""
    from job_scheduler.services.task_service import create_task

    name = f"duplicate-task-{uuid.uuid4().hex[:8]}"
    data = TaskCreate(name=name)

    # First creation should succeed
    task1 = await create_task(session, data)
    assert task1.name == name

    # Second creation with same name should also succeed
    task2 = await create_task(session, data)
    assert task2.name == name
    assert task2.task_id != task1.task_id


@pytest.mark.asyncio
async def test_create_task_invalid_max_retries(session):
    """Negative max_retries should be rejected by schema validation."""
    from job_scheduler.services.task_service import create_task
    from pydantic import ValidationError

    name = f"invalid-retries-{uuid.uuid4().hex[:8]}"

    # Negative max_retries should fail validation
    with pytest.raises(ValidationError, match="max_retries"):
        data = TaskCreate(name=name, max_retries=-1)
        await create_task(session, data)


@pytest.mark.asyncio
async def test_create_task_invalid_timeout(session):
    """Zero or negative timeout should be rejected by schema validation."""
    from job_scheduler.services.task_service import create_task
    from pydantic import ValidationError

    name = f"invalid-timeout-{uuid.uuid4().hex[:8]}"

    # Zero timeout should fail validation
    with pytest.raises(ValidationError, match="timeout_sec"):
        data = TaskCreate(name=name, timeout_sec=0)
        await create_task(session, data)


@pytest.mark.asyncio
async def test_get_task(session):
    from job_scheduler.services.task_service import create_task, get_task

    name = f"get-test-task-{uuid.uuid4().hex[:8]}"
    data = TaskCreate(name=name)
    task = await create_task(session, data)
    fetched = await get_task(session, task.task_id)
    assert fetched is not None
    assert fetched.task_id == task.task_id


@pytest.mark.asyncio
async def test_get_task_not_found(session):
    from job_scheduler.services.task_service import get_task

    result = await get_task(session, uuid.uuid4())
    assert result is None
