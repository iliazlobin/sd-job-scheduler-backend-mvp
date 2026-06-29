"""Black-box functional test configuration for Job Scheduler MVP.

Talks to the RUNNING system via API_BASE_URL. No app imports.
"""

import os
import uuid
import pytest
import httpx


@pytest.fixture(scope="session")
def api_base_url() -> str:
    url = os.environ.get("API_BASE_URL", "http://localhost:8010")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def client(api_base_url: str) -> httpx.Client:
    return httpx.Client(base_url=api_base_url, timeout=30)


@pytest.fixture(scope="session")
def task_factory(client: httpx.Client):
    """Create a task and return its task_id. Cleans up nothing (functional tests own their data)."""

    def _create(
        name: str = "functional-test-task", max_retries: int = 3, timeout_sec: int = 3600
    ) -> dict:
        # Append UUID to ensure uniqueness across test runs
        unique_name = f"{name}-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/tasks",
            json={
                "name": unique_name,
                "max_retries": max_retries,
                "timeout_sec": timeout_sec,
            },
        )
        assert resp.status_code == 201, f"task creation failed: {resp.text}"
        return resp.json()

    return _create


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: test that waits for scheduler execution (may take several seconds)"
    )
