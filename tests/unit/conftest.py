"""Unit test configuration — white-box tests that import the app."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from job_scheduler.main import create_app


@pytest_asyncio.fixture
async def session():
    """Create a test session."""
    from job_scheduler.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    async with async_sessionmaker(engine, expire_on_commit=False)() as sess:
        yield sess
    await engine.dispose()


@pytest_asyncio.fixture
async def app():
    """Create a test app instance."""
    application = create_app()
    yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
