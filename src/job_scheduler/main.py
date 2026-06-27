"""FastAPI application factory + lifespan + /healthz."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from job_scheduler.routers.tasks import router as tasks_router
from job_scheduler.routers.jobs import router as jobs_router
from job_scheduler.services.scheduler import Scheduler

logger = logging.getLogger(__name__)

_scheduler = Scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logging.basicConfig(level=logging.INFO)
    await _scheduler.start()
    logger.info("Application started")
    yield
    # Shutdown
    await _scheduler.stop()
    logger.info("Application stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Job Scheduler", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    app.include_router(tasks_router)
    app.include_router(jobs_router)

    return app


app = create_app()
