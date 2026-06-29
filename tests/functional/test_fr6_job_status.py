"""FR-6: Query job status.

Client retrieves current state of any job.
GET /jobs/{job_id} → 200; unknown job → 404.
"""


def test_get_job_status(client, task_factory):
    """GET /jobs/{id} returns full job metadata."""
    task = task_factory(name="fr6-status")
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    created = resp.json()
    job_id = created["job_id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["task_id"] == task["task_id"]
    assert data["status"] in ("PENDING", "CLAIMED", "RUNNING", "SUCCESS", "FAILED", "CANCELLED")
    assert "scheduled_at" in data
    assert "retry_count" in data
    assert "created_at" in data
    # Should include task name for display
    assert "task_name" in data, f"response missing task_name: {data}"


def test_get_job_not_found(client):
    """Unknown job_id returns 404."""
    resp = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
