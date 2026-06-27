"""Task router — POST /tasks."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from job_scheduler.database import get_session
from job_scheduler.schemas.task import TaskCreate, TaskResponse
from job_scheduler.services import task_service

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(body: TaskCreate, session: AsyncSession = Depends(get_session)):
    task = await task_service.create_task(session, body)
    return TaskResponse.model_validate(task)
