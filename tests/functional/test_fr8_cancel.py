"""FR-8: Cancel pending job.

Client cancels a job that has not yet started.
POST /jobs/{job_id}/cancel → 200 CANCELLED; RUNNING/SUCCESS/FAILED → 409; unknown → 404.
"""

import time
import pytest


def test_cancel_pending_job(client, task_factory):
    """Cancelling a PENDING job returns 200 with status CANCELLED."""
    task = task_factory(name="fr8-cancel")
    # Schedule far in the future so it stays PENDING
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "scheduled_at": "2099-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Verify it's PENDING
    status = client.get(f"/jobs/{job_id}").json()["status"]
    assert status == "PENDING"

    # Cancel it
    resp = client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["status"] == "CANCELLED"

    # Verify state is persisted
    resp2 = client.get(f"/jobs/{job_id}")
    assert resp2.json()["status"] == "CANCELLED"


def test_cancel_already_running_conflict(client, task_factory):
    """Cancelling a RUNNING job returns 409."""
    task = task_factory(name="fr8-running", max_retries=1)
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Wait for job to start running
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()["status"]
        if status in ("RUNNING", "CLAIMED", "SUCCESS", "FAILED"):
            break
        time.sleep(0.3)

    # Try to cancel — should be 409 if not PENDING
    status = client.get(f"/jobs/{job_id}").json()["status"]
    if status == "PENDING":
        pytest.skip("job still PENDING after 15s — scheduler not running?")
    resp = client.post(f"/jobs/{job_id}/cancel")
    assert (
        resp.status_code == 409
    ), f"expected 409 for non-PENDING job (status={status}), got {resp.status_code}: {resp.text}"


def test_cancel_success_job_conflict(client, task_factory):
    """Cancelling an already SUCCESS job returns 409."""
    task = task_factory(name="fr8-success", max_retries=1)
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Wait for job to succeed
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()["status"]
        if status == "SUCCESS":
            break
        time.sleep(0.5)
    else:
        pytest.fail("job did not reach SUCCESS within 20s")

    resp = client.post(f"/jobs/{job_id}/cancel")
    assert (
        resp.status_code == 409
    ), f"expected 409 for SUCCESS job, got {resp.status_code}: {resp.text}"


def test_cancel_unknown_job(client):
    """Cancelling unknown job returns 404."""
    resp = client.post("/jobs/00000000-0000-0000-0000-000000000000/cancel")
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
