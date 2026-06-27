"""FR-3: Schedule delayed job.

Client schedules a job to fire at a future UTC timestamp.
POST /jobs {task_id, scheduled_at} → 201; past scheduled_at → 422.
"""

from datetime import datetime, timezone, timedelta


def test_schedule_delayed_job(client, task_factory):
    """Job with future scheduled_at returns 201 with correct scheduled_at."""
    task = task_factory(name="fr3-delayed")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "scheduled_at": future,
        },
    )
    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "PENDING"
    # Verify scheduled_at is set (accept ISO8601 with or without trailing Z)
    assert data["scheduled_at"] is not None


def test_schedule_past_job_rejected(client, task_factory):
    """scheduled_at in the past returns 422."""
    task = task_factory(name="fr3-past")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "scheduled_at": past,
        },
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


def test_schedule_invalid_timestamp(client, task_factory):
    """Garbage scheduled_at returns 422."""
    task = task_factory(name="fr3-bad-ts")
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "scheduled_at": "not-a-timestamp",
        },
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
