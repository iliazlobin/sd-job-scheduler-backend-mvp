"""FR-2: Schedule immediate job.

Client schedules a job to execute as soon as possible under a task.
POST /jobs {task_id, params?, idempotency_key?} → 201.
"""


def test_schedule_immediate_job(client, task_factory):
    """Immediate job (no scheduled_at) returns 201 with status PENDING."""
    task = task_factory(name="fr2-immediate")
    resp = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "params": {"key": "value"},
        },
    )
    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "job_id" in data
    assert data["task_id"] == task["task_id"]
    assert data["status"] == "PENDING"
    assert data["params"] == {"key": "value"}
    assert "scheduled_at" in data
    assert data["retry_count"] == 0
    assert "created_at" in data


def test_schedule_immediate_job_minimal(client, task_factory):
    """Immediate job with only task_id works (params optional)."""
    task = task_factory(name="fr2-minimal")
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["params"] is not None


def test_schedule_job_unknown_task(client):
    """Non-existent task_id returns 404."""
    resp = client.post(
        "/jobs",
        json={
            "task_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"


def test_schedule_job_missing_task_id(client):
    """Missing task_id returns 422."""
    resp = client.post("/jobs", json={"params": {}})
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
