"""FR-4: Idempotent job creation.

Duplicate idempotency_key returns the existing job (200), not a new one.
Second call returns same job_id.
"""

import uuid


def test_idempotent_job_creation(client, task_factory):
    """Same idempotency_key twice → same job_id, second call returns 200."""
    task = task_factory(name="fr4-idempotent")
    # Use unique key per test run to avoid conflicts with previous runs
    idem_key = f"fr4-key-001-{uuid.uuid4().hex[:8]}"
    payload = {
        "task_id": task["task_id"],
        "params": {"x": 1},
        "idempotency_key": idem_key,
    }

    # First call — creates new job
    resp1 = client.post("/jobs", json=payload)
    assert (
        resp1.status_code == 201
    ), f"first call expected 201, got {resp1.status_code}: {resp1.text}"
    job1 = resp1.json()
    assert "job_id" in job1

    # Second call — same key, returns existing job
    resp2 = client.post("/jobs", json=payload)
    assert (
        resp2.status_code == 200
    ), f"second call expected 200, got {resp2.status_code}: {resp2.text}"
    job2 = resp2.json()
    assert (
        job2["job_id"] == job1["job_id"]
    ), f"idempotency key should return same job: {job1['job_id']} != {job2['job_id']}"
    assert job2["task_id"] == task["task_id"]


def test_idempotent_same_key_different_params(client, task_factory):
    """Same idempotency_key with different params still returns existing job (params ignored on duplicate)."""
    task = task_factory(name="fr4-diff-params")
    # Use unique key per test run
    key = f"fr4-key-002-{uuid.uuid4().hex[:8]}"

    resp1 = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "idempotency_key": key,
            "params": {"original": True},
        },
    )
    assert resp1.status_code == 201
    job1 = resp1.json()

    resp2 = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "idempotency_key": key,
            "params": {"hijack": "attempt"},
        },
    )
    # Second call returns existing job (200, no new creation)
    assert resp2.status_code == 200, f"expected 200, got {resp2.status_code}: {resp2.text}"
    assert resp2.json()["job_id"] == job1["job_id"]


def test_different_keys_create_different_jobs(client, task_factory):
    """Different idempotency_keys create different jobs."""
    task = task_factory(name="fr4-different")
    # Use unique keys per test run
    run_id = uuid.uuid4().hex[:8]
    key_a = f"fr4-key-A-{run_id}"
    key_b = f"fr4-key-B-{run_id}"

    resp1 = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "idempotency_key": key_a,
        },
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        "/jobs",
        json={
            "task_id": task["task_id"],
            "idempotency_key": key_b,
        },
    )
    assert resp2.status_code == 201

    assert resp1.json()["job_id"] != resp2.json()["job_id"]
