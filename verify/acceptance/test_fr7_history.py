"""FR-7: Query execution history.

Client retrieves the append-only event log for a job.
GET /jobs/{job_id}/history → 200 with pagination; unknown job → 404.
"""

import time


def test_get_history_for_new_job(client, task_factory):
    """Newly created job has at least the ENQUEUED event."""
    task = task_factory(name="fr7-history")
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    resp = client.get(f"/jobs/{job_id}/history")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "events" in data
    assert "job_id" in data
    assert "total" in data
    assert data["job_id"] == job_id
    event_types = [e["event_type"] for e in data["events"]]
    assert "ENQUEUED" in event_types, f"new job must emit ENQUEUED event: {event_types}"


def test_history_pagination(client, task_factory):
    """History endpoint supports offset/limit pagination."""
    task = task_factory(name="fr7-page", max_retries=0)
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Wait for execution to produce multiple events
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()["status"]
        if status == "SUCCESS":
            break
        time.sleep(0.5)

    # Get first page
    resp = client.get(f"/jobs/{job_id}/history?offset=0&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) <= 2
    assert data["offset"] == 0
    assert data["limit"] == 2
    assert data["total"] >= 2

    # Get second page
    resp2 = client.get(f"/jobs/{job_id}/history?offset=2&limit=2")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["offset"] == 2

    # First events of each page must differ
    if data["events"] and data2["events"]:
        assert data["events"][0]["event_id"] != data2["events"][0]["event_id"]


def test_history_default_pagination(client, task_factory):
    """History with no query params uses defaults (offset=0, limit=20)."""
    task = task_factory(name="fr7-defaults")
    resp = client.post("/jobs", json={"task_id": task["task_id"]})
    job_id = resp.json()["job_id"]

    resp = client.get(f"/jobs/{job_id}/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["offset"] == 0
    assert data["limit"] == 20


def test_history_unknown_job(client):
    """Unknown job_id returns 404."""
    resp = client.get("/jobs/00000000-0000-0000-0000-000000000000/history")
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
