"""White-box tests for Pydantic schema validation rules."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from job_scheduler.schemas import (
    CancelResponse,
    EventResponse,
    HistoryResponse,
    JobCreate,
    JobResponse,
    JobStatusResponse,
    TaskCreate,
    TaskResponse,
)


# ── TaskCreate ──────────────────────────────────────────────────────────────


class TestTaskCreate:
    def test_valid_minimal(self):
        t = TaskCreate(name="send-email")
        assert t.name == "send-email"
        assert t.max_retries == 3
        assert t.timeout_sec == 3600

    def test_valid_full(self):
        t = TaskCreate(name="process-payment", max_retries=5, timeout_sec=120)
        assert t.max_retries == 5
        assert t.timeout_sec == 120

    def test_max_retries_zero_accepted(self):
        """max_retries=0 is accepted — means no retries (FR-9)."""
        t = TaskCreate(name="no-retry", max_retries=0)
        assert t.max_retries == 0

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError, match="name"):
            TaskCreate(name="")

    def test_rejects_negative_max_retries(self):
        with pytest.raises(ValidationError, match="max_retries"):
            TaskCreate(name="bad", max_retries=-1)

    def test_rejects_zero_timeout(self):
        with pytest.raises(ValidationError, match="timeout_sec"):
            TaskCreate(name="bad", timeout_sec=0)

    def test_rejects_negative_timeout(self):
        with pytest.raises(ValidationError, match="timeout_sec"):
            TaskCreate(name="bad", timeout_sec=-10)


# ── TaskResponse ────────────────────────────────────────────────────────────


class TestTaskResponse:
    def test_construct(self):
        tid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        r = TaskResponse(task_id=tid, name="x", max_retries=3, timeout_sec=60, created_at=now)
        assert r.task_id == tid
        assert r.created_at == now


# ── JobCreate ───────────────────────────────────────────────────────────────


class TestJobCreate:
    def test_valid_immediate(self):
        tid = uuid.uuid4()
        j = JobCreate(task_id=tid)
        assert j.task_id == tid
        assert j.scheduled_at is None
        assert j.params == {}
        assert j.idempotency_key is None

    def test_valid_with_params(self):
        j = JobCreate(
            task_id=uuid.uuid4(),
            params={"to": "user@example.com"},
            idempotency_key="key-01",
        )
        assert j.params == {"to": "user@example.com"}
        assert j.idempotency_key == "key-01"

    def test_valid_future_scheduled_at(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        j = JobCreate(task_id=uuid.uuid4(), scheduled_at=future)
        assert j.scheduled_at == future

    def test_rejects_past_scheduled_at(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(ValidationError, match="scheduled_at"):
            JobCreate(task_id=uuid.uuid4(), scheduled_at=past)

    def test_rejects_naive_scheduled_at(self):
        naive = datetime.now()  # no tzinfo
        with pytest.raises(ValidationError, match="scheduled_at"):
            JobCreate(task_id=uuid.uuid4(), scheduled_at=naive)

    def test_rejects_invalid_task_id_string(self):
        with pytest.raises(ValidationError):
            JobCreate(task_id="not-a-uuid")

    def test_accepts_uuid_string(self):
        tid = uuid.uuid4()
        j = JobCreate(task_id=str(tid))
        assert j.task_id == tid


# ── JobResponse ─────────────────────────────────────────────────────────────


class TestJobResponse:
    def test_construct_minimal(self):
        now = datetime.now(timezone.utc)
        r = JobResponse(
            job_id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            status="PENDING",
            scheduled_at=now,
            params={},
            created_at=now,
        )
        assert r.status == "PENDING"
        assert r.started_at is None
        assert r.completed_at is None
        assert r.retry_count == 0


# ── JobStatusResponse ───────────────────────────────────────────────────────


class TestJobStatusResponse:
    def test_construct(self):
        now = datetime.now(timezone.utc)
        r = JobStatusResponse(
            job_id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            task_name="send-email",
            status="RUNNING",
            scheduled_at=now,
            params={},
            retry_count=1,
            max_retries=3,
            created_at=now,
        )
        assert r.task_name == "send-email"
        assert r.max_retries == 3


# ── CancelResponse ──────────────────────────────────────────────────────────


class TestCancelResponse:
    def test_construct(self):
        r = CancelResponse(job_id=uuid.uuid4(), status="CANCELLED")
        assert r.status == "CANCELLED"


# ── EventResponse ───────────────────────────────────────────────────────────


class TestEventResponse:
    def test_construct(self):
        now = datetime.now(timezone.utc)
        e = EventResponse(
            event_id=1,
            job_id=uuid.uuid4(),
            event_type="ENQUEUED",
            timestamp=now,
            payload={},
        )
        assert e.event_id == 1
        assert e.event_type == "ENQUEUED"
        assert e.job_id is not None


# ── HistoryResponse ─────────────────────────────────────────────────────────


class TestHistoryResponse:
    def test_construct_empty(self):
        datetime.now(timezone.utc)
        h = HistoryResponse(
            job_id=uuid.uuid4(),
            events=[],
            total=0,
            offset=0,
            limit=20,
        )
        assert h.events == []
        assert h.total == 0

    def test_construct_with_events(self):
        jid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        events = [
            EventResponse(event_id=1, job_id=jid, event_type="ENQUEUED", timestamp=now, payload={}),
            EventResponse(event_id=2, job_id=jid, event_type="CLAIMED", timestamp=now, payload={}),
        ]
        h = HistoryResponse(job_id=jid, events=events, total=2, offset=0, limit=20)
        assert len(h.events) == 2
        assert h.total == 2


# ── Imports ─────────────────────────────────────────────────────────────────


class TestSchemaImports:
    """All schemas must import cleanly from the package root."""

    def test_all_exports(self):
        from job_scheduler import schemas

        expected = {
            "TaskCreate",
            "TaskResponse",
            "JobCreate",
            "JobResponse",
            "JobStatusResponse",
            "CancelResponse",
            "EventResponse",
            "HistoryResponse",
        }
        assert set(schemas.__all__) == expected

    def test_individual_imports(self):
        from job_scheduler.schemas.task import TaskCreate, TaskResponse
        from job_scheduler.schemas.job import (
            JobCreate,
            JobResponse,
            JobStatusResponse,
            CancelResponse,
        )
        from job_scheduler.schemas.event import EventResponse, HistoryResponse

        # If we got here, all imports succeeded
        assert TaskCreate is not None
        assert TaskResponse is not None
        assert JobCreate is not None
        assert JobResponse is not None
        assert JobStatusResponse is not None
        assert CancelResponse is not None
        assert EventResponse is not None
        assert HistoryResponse is not None
