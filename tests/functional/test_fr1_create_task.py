"""FR-1: Create task definition.

Client defines a task with name, max_retries, and timeout_sec.
POST /tasks → 201; missing name → 422.
"""


def test_create_task_success(client):
    """Valid task creation returns 201 with task fields."""
    resp = client.post(
        "/tasks",
        json={
            "name": "fr1-test-task",
            "max_retries": 5,
            "timeout_sec": 7200,
        },
    )
    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "task_id" in data
    assert data["name"] == "fr1-test-task"
    assert data["max_retries"] == 5
    assert data["timeout_sec"] == 7200
    assert "created_at" in data


def test_create_task_defaults(client):
    """Task creation with only name uses defaults (max_retries=3, timeout_sec=3600)."""
    resp = client.post("/tasks", json={"name": "fr1-defaults"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "fr1-defaults"
    assert data["max_retries"] == 3
    assert data["timeout_sec"] == 3600


def test_create_task_missing_name(client):
    """Missing required 'name' field returns 422."""
    resp = client.post("/tasks", json={"max_retries": 3})
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


def test_create_task_empty_name(client):
    """Empty 'name' returns 422."""
    resp = client.post("/tasks", json={"name": ""})
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


def test_create_task_invalid_max_retries(client):
    """Negative max_retries returns 422."""
    resp = client.post("/tasks", json={"name": "bad-retries", "max_retries": -1})
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
