"""FR-9: Retry failed jobs.

Failed jobs are automatically retried up to max_retries with exponential backoff.
Job transitions FAILED → PENDING with next_retry_at set; after max_retries exhausted → stays FAILED.
"""

import time
import pytest


@pytest.mark.slow
def test_retry_loop_backoff(client, task_factory):
    """Job with max_retries=2 + fail=true retries with backoff, then stays FAILED."""
    task = task_factory(name="fr9-retry", max_retries=2, timeout_sec=3600)
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "params": {"fail": True},
        },
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Poll the job through its lifecycle. We expect:
    # 1. Initial execution → FAILED (retry_count 0, retries remaining)
    # 2. First retry after backoff → FAILED (retry_count 1, retries remaining)
    # 3. Second retry after backoff → FAILED (retry_count 2, no retries left)
    # 4. Final state: FAILED, retry_count=2

    deadline = time.monotonic() + 60  # generous: 2 retries with backoff (1s + 2s) + execution
    final_retry_count = None

    while time.monotonic() < deadline:
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        status = data["status"]
        retry_count = data.get("retry_count", 0)

        if (
            status == "RETRYING" or retry_count > final_retry_count
            if final_retry_count is not None
            else False
        ):
            pass

        final_retry_count = max(final_retry_count or 0, retry_count)

        if status == "FAILED" and retry_count == task["max_retries"]:
            # Terminal FAILED after exhausting retries
            break

        time.sleep(1.0)

    # Verify final state
    data = client.get(f"/jobs/{job_id}").json()
    assert data["status"] == "FAILED", f"expected terminal FAILED, got {data['status']}"
    assert (
        data["retry_count"] == task["max_retries"]
    ), f"expected retry_count {task['max_retries']}, got {data['retry_count']}"

    # Verify retry events exist in history
    history = client.get(f"/jobs/{job_id}/history").json()
    event_types = [e["event_type"] for e in history["events"]]
    assert "RETRYING" in event_types, f"expected RETRYING events in history, got: {event_types}"
    # FAILED events should appear (one per attempt, final one at end)
    failed_count = event_types.count("FAILED")
    assert (
        failed_count >= task["max_retries"] + 1
    ), f"expected at least {task['max_retries'] + 1} FAILED events (initial + retries), got {failed_count}"


@pytest.mark.slow
def test_retry_keeps_job_pending_during_backoff(client, task_factory):
    """Between retries, job is PENDING with next_retry_at set (backoff window)."""
    task = task_factory(name="fr9-backoff-window", max_retries=3, timeout_sec=3600)
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "params": {"fail": True},
        },
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + 45
    saw_pending_with_next_retry = False

    while time.monotonic() < deadline:
        data = client.get(f"/jobs/{job_id}").json()
        status = data["status"]
        if status == "PENDING" and data.get("next_retry_at") is not None:
            saw_pending_with_next_retry = True
            break
        if status == "FAILED" and data.get("retry_count", 0) == task["max_retries"]:
            break
        time.sleep(0.5)

    assert (
        saw_pending_with_next_retry
    ), "job never entered PENDING state with next_retry_at set during retry backoff"


def test_no_retry_when_max_retries_zero(client, task_factory):
    """Job with max_retries=0 that fails stays FAILED immediately, no retries."""
    task = task_factory(name="fr9-no-retry", max_retries=0, timeout_sec=3600)
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "params": {"fail": True},
        },
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()["status"]
        if status == "FAILED":
            break
        time.sleep(0.5)
    else:
        pytest.fail("job did not reach FAILED within 20s")

    data = client.get(f"/jobs/{job_id}").json()
    assert data["retry_count"] == 0
    assert data["status"] == "FAILED"

    # No RETRYING events should exist
    history = client.get(f"/jobs/{job_id}/history").json()
    event_types = [e["event_type"] for e in history["events"]]
    assert "RETRYING" not in event_types, f"expected no RETRYING events, got: {event_types}"
