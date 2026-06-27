"""Task service — create and retrieve task definitions."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_scheduler.models.task import Task
from job_scheduler.schemas.task import TaskCreate


async def create_task(session: AsyncSession, data: TaskCreate) -> Task:
    task = Task(name=data.name, max_retries=data.max_retries, timeout_sec=data.timeout_sec)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
    result = await session.execute(select(Task).where(Task.task_id == task_id))
    return result.scalar_one_or_none()
