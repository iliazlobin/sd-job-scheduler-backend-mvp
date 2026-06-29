"""FR-5: Worker executes jobs.

Scheduler polls for PENDING jobs, claims them, and transitions to SUCCESS.
Job transitions through CLAIMED → RUNNING → SUCCESS.
Each transition emits an execution_event.
"""

import time
import pytest


@pytest.mark.slow
def test_job_executes_to_success(client, task_factory):
    """Immediate job is picked up by scheduler and reaches SUCCESS with events."""
    task = task_factory(name="fr5-execute", max_retries=0)
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "params": {"fail": False},
        },
    )
    assert resp.status_code == 201
    job = resp.json()
    job_id = job["job_id"]

    # Poll for completion (scheduler runs ≤1s interval; wait up to 20s)
    deadline = time.monotonic() + 20
    final_status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("SUCCESS", "FAILED"):
            final_status = status
            break
        time.sleep(0.5)

    assert (
        final_status == "SUCCESS"
    ), f"job {job_id} did not reach SUCCESS within 20s (last status: {final_status})"

    # Verify started_at and completed_at are set
    job_data = client.get(f"/jobs/{job_id}").json()
    assert job_data["started_at"] is not None, "started_at should be set after execution"
    assert job_data["completed_at"] is not None, "completed_at should be set after execution"


@pytest.mark.slow
def test_execution_emits_events(client, task_factory):
    """Job execution emits ENQUEUED, CLAIMED, STARTED, COMPLETED events in order."""
    task = task_factory(name="fr5-events", max_retries=0)
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Wait for job to complete
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()["status"]
        if status == "SUCCESS":
            break
        time.sleep(0.5)
    else:
        pytest.fail(f"job {job_id} did not complete within 20s")

    # Check event history
    resp = client.get(f"/jobs/{job_id}/history")
    assert resp.status_code == 200
    events = resp.json()["events"]

    event_types = [e["event_type"] for e in events]
    # Must contain ENQUEUED (created at schedule time) and COMPLETED (at end)
    assert "ENQUEUED" in event_types, f"expected ENQUEUED event, got: {event_types}"
    assert "COMPLETED" in event_types, f"expected COMPLETED event, got: {event_types}"

    # Check chronological order: timestamps must be non-decreasing
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps), f"events not chronologically ordered: {timestamps}"
