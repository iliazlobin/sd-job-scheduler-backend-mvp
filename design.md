# Job Scheduler MVP — Design

## Architecture overview

Single-process scheduler + worker on FastAPI + PostgreSQL. The app serves HTTP (tasks, jobs, history, cancellation) and runs a background asyncio polling loop to claim + execute PENDING jobs. No sharding, no Kafka, no timing wheel — the MVP is vertically scaled, DB-polled, in-process.

```
                    POST/GET
Client ─────────────────────► FastAPI (uvicorn)
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              routers/        lifespan        routers/
            (tasks/jobs)     (background)    (jobs/cancel
                    │         scheduler        /history)
                    │              │              │
                    ▼              ▼              ▼
              services/      services/      services/
            task_service  scheduler loop  job_service
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                                   ▼
                           PostgreSQL 16
                     ┌─────────────────────────┐
                     │  tasks  │  jobs  │ exec_ │
                     │         │        │ events│
                     └─────────────────────────┘
```

**Data flow per endpoint:**

1. `POST /tasks` — parse body → validate name non-empty → INSERT task row → return 201 + task DTO
2. `POST /jobs` — parse body → look up task (404 if missing) → check idempotency_key (if present, return existing 200) → validate scheduled_at (422 if past) → INSERT job row + emit ENQUEUED event → return 201 (200 if idempotent match)
3. `GET /jobs/{id}` — SELECT job + task name → 200 or 404
4. `GET /jobs/{id}/history` — SELECT events ORDER BY timestamp, paginated → 200 or 404
5. `POST /jobs/{id}/cancel` — SELECT job → if PENDING update to CANCELLED + emit event → 200; if RUNNING/SUCCESS/FAILED → 409; missing → 404
6. **Scheduler loop** (background asyncio task, ≤1s interval):
   - `SELECT … FROM jobs WHERE status='PENDING' AND scheduled_at <= NOW() FOR UPDATE SKIP LOCKED LIMIT 1`
   - execute (simulated: `await asyncio.sleep(0.01)`) → if `params.fail` is truthy, treat as failure; otherwise success → on success: UPDATE status=SUCCESS, emit COMPLETED event → on failure: check retry_count < task.max_retries → UPDATE status=PENDING with exponential backoff next_retry_at, emit RETRYING event; if retries exhausted → stay FAILED, emit FAILED event

## Data model

```sql
-- Enum types
CREATE TYPE job_status AS ENUM ('PENDING','CLAIMED','RUNNING','SUCCESS','FAILED','CANCELLED');
CREATE TYPE event_type AS ENUM ('ENQUEUED','CLAIMED','STARTED','COMPLETED','FAILED','RETRYING','CANCELLED');

-- Task definition
CREATE TABLE tasks (
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    max_retries INT NOT NULL DEFAULT 3,
    timeout_sec INT NOT NULL DEFAULT 3600,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Job instance
CREATE TABLE jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(task_id),
    status job_status NOT NULL DEFAULT 'PENDING',
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    params JSONB DEFAULT '{}',
    idempotency_key VARCHAR(255) UNIQUE,    -- client-generated dedup guard
    retry_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_jobs_status_scheduled ON jobs(status, scheduled_at)
    WHERE status = 'PENDING';               -- scheduler poll index

-- Append-only event log
CREATE TABLE execution_events (
    event_id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(job_id),
    event_type event_type NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB DEFAULT '{}'
);
CREATE INDEX idx_events_job_time ON execution_events(job_id, timestamp);
```

**Key design choices:**
- `job_status` has CLAIMED between PENDING and RUNNING — prevents double-claim in the window between SELECT and UPDATE
- `idempotency_key` UNIQUE constraint is the deduplication mechanism — INSERT collision triggers the "return existing" path
- Partial index `idx_jobs_status_scheduled` covers only PENDING rows — keeps the scheduler's main query lean
- `next_retry_at` stays null until a FAILED job is re-queued; scheduler checks `scheduled_at` for initial and `next_retry_at` for retries

## API contracts

### `POST /tasks`
Create a task definition.

**Request:**
```json
{
  "name": "send-email",
  "max_retries": 3,
  "timeout_sec": 3600
}
```

**Response 201:**
```json
{
  "task_id": "uuid",
  "name": "send-email",
  "max_retries": 3,
  "timeout_sec": 3600,
  "created_at": "2026-06-27T12:00:00Z"
}
```

**Errors:**
- `422` — missing `name`, non-positive `max_retries`, non-positive `timeout_sec`

---

### `POST /jobs`
Schedule a job under a task. Immediate (omit `scheduled_at`) or delayed (provide future ISO8601 `scheduled_at`). Optional `idempotency_key` for dedup.

**Request (immediate):**
```json
{
  "task_id": "uuid",
  "params": {"to": "user@example.com"},
  "idempotency_key": "client-key-01"
}
```

**Request (delayed):**
```json
{
  "task_id": "uuid",
  "scheduled_at": "2026-06-27T13:00:00Z",
  "params": {"to": "user@example.com"}
}
```

**Response 201 (new job):**
```json
{
  "job_id": "uuid",
  "task_id": "uuid",
  "status": "PENDING",
  "scheduled_at": "2026-06-27T13:00:00Z",
  "params": {"to": "user@example.com"},
  "idempotency_key": "client-key-01",
  "retry_count": 0,
  "created_at": "2026-06-27T12:00:00Z"
}
```

**Response 200 (idempotent duplicate):** same body as 201, but status reflects current state. Second call with same `idempotency_key` returns the existing job — no new row inserted.

**Errors:**
- `422` — `task_id` missing, `scheduled_at` in the past, unknown enum values
- `404` — `task_id` references unknown task

---

### `GET /jobs/{job_id}`
Get current job state + task metadata.

**Response 200:**
```json
{
  "job_id": "uuid",
  "task_id": "uuid",
  "task_name": "send-email",
  "status": "RUNNING",
  "scheduled_at": "2026-06-27T13:00:00Z",
  "started_at": "2026-06-27T13:00:01Z",
  "completed_at": null,
  "params": {"to": "user@example.com"},
  "retry_count": 0,
  "max_retries": 3,
  "created_at": "2026-06-27T12:00:00Z"
}
```

**Errors:**
- `404` — unknown job_id

---

### `GET /jobs/{job_id}/history`
Paginated execution event timeline. Chronologically ordered (oldest first).

**Query params:** `?offset=0&limit=20` (both optional, 0-based offset, max 100)

**Response 200:**
```json
{
  "job_id": "uuid",
  "events": [
    {
      "event_id": 1,
      "event_type": "ENQUEUED",
      "timestamp": "2026-06-27T12:00:00Z",
      "payload": {}
    },
    {
      "event_id": 2,
      "event_type": "CLAIMED",
      "timestamp": "2026-06-27T13:00:00Z",
      "payload": {}
    }
  ],
  "offset": 0,
  "limit": 20,
  "total": 2
}
```

**Errors:**
- `404` — unknown job_id

---

### `POST /jobs/{job_id}/cancel`
Cancel a PENDING job. Has no effect on already-started or terminal jobs.

**Response 200:**
```json
{
  "job_id": "uuid",
  "status": "CANCELLED"
}
```

**Errors:**
- `404` — unknown job_id
- `409` — job is RUNNING, SUCCESS, or FAILED (not cancellable)

---

### `GET /healthz`
Liveness/readiness probe for compose healthcheck and e2e verifier.

**Response 200:** `{"status": "ok"}`

## Module / file layout

```
sd-job-scheduler-backend-mvp/
├── src/job_scheduler/           # Application package
│   ├── __init__.py
│   ├── main.py                  # create_app() factory, lifespan (start/stop scheduler), /healthz
│   ├── config.py                # pydantic-settings: DATABASE_URL, SCHEDULER_POLL_INTERVAL_MS
│   ├── database.py              # async engine + async_session factory, get_session dependency
│   ├── models/
│   │   ├── __init__.py
│   │   ├── task.py              # SQLAlchemy Task model
│   │   ├── job.py               # SQLAlchemy Job model + JobStatus enum
│   │   └── event.py             # SQLAlchemy ExecutionEvent model + EventType enum
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── task.py              # TaskCreate, TaskResponse
│   │   ├── job.py               # JobCreate, JobResponse, JobStatusResponse, CancelResponse
│   │   └── event.py             # EventResponse, HistoryResponse
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── tasks.py             # POST /tasks — thin: parse → service → serialize
│   │   └── jobs.py              # POST /jobs, GET /jobs/{id}, GET /jobs/{id}/history, POST /jobs/{id}/cancel
│   └── services/
│       ├── __init__.py
│       ├── task_service.py      # create_task, get_task
│       ├── job_service.py       # create_job (with idempotency), get_job, get_history, cancel_job
│       └── scheduler.py         # Scheduler class: polling loop, claim, execute, retry logic
├── alembic/
│   ├── env.py
│   ├── versions/
│   │   └── 001_initial.py       # tasks, jobs, execution_events tables + indexes + enums
│   └── script.mako
├── alembic.ini
├── tests/                       # White-box (imports app)
│   ├── conftest.py              # test DB engine/session, app fixture
│   ├── test_task_service.py
│   ├── test_job_service.py
│   └── test_scheduler.py
├── verify/                      # Black-box acceptance (HTTP only, no app imports)
│   ├── manifest.env
│   └── acceptance/
│       ├── conftest.py
│       ├── test_fr1_create_task.py
│       ├── test_fr2_immediate_job.py
│       ├── test_fr3_delayed_job.py
│       ├── test_fr4_idempotency.py
│       ├── test_fr5_execution.py
│       ├── test_fr6_job_status.py
│       ├── test_fr7_history.py
│       ├── test_fr8_cancel.py
│       └── test_fr9_retry.py
├── Dockerfile                   # multi-stage, python:3.12-slim
├── docker-compose.yml           # db + app, APP_PORT:-8010:8000, healthchecks
├── pyproject.toml               # deps + dev extras
├── .env.example
├── .gitignore
├── README.md
├── DEPLOY.md
├── KICKOFF.md
├── AGENTS.md
└── design.md                    # this file
```

## Key design decisions

1. **Single-process scheduler + worker.** The background asyncio task polls + executes in the same process as the FastAPI server. Tradeoff: no horizontal scaling, but zero operational complexity for MVP. The full design moves to a separate worker fleet via Kafka.

2. **DB polling with `SKIP LOCKED`.** The scheduler does `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` to claim one job per tick. This avoids the timing-wheel complexity while remaining correct under a single claimer. Poll interval is configurable (default 500ms).

3. **CLAIMED intermediate state.** Prevents a race where two concurrent SELECTs pick the same job. The transition is: PENDING → CLAIMED → RUNNING → SUCCESS/FAILED. CLAIMED is an atomic `UPDATE … WHERE job_id=? AND status='PENDING'`; if rows affected = 0, another claimant already took it.

4. **Idempotency via UNIQUE constraint.** The `idempotency_key` column has a UNIQUE constraint. On duplicate, the INSERT raises IntegrityError; the service catches it and returns the existing job. No application-level lock needed — the DB guarantees uniqueness.

5. **Exponential backoff retries.** On FAILED, if `retry_count < task.max_retries`: set `next_retry_at = NOW() + (2^retry_count) seconds`, status back to PENDING, increment retry_count, emit RETRYING event. The scheduler's poll query uses `LEAST(scheduled_at, next_retry_at)` as the effective fire time.

6. **Partial index on PENDING jobs.** `CREATE INDEX … WHERE status = 'PENDING'` keeps the index small and scans fast regardless of total job history volume.

## Implementation tasks

### Tier: staff-engineer

| # | Task | File(s) | Rationale |
|---|---|---|---|
| S1 | Data model + Alembic migration | `models/*`, `alembic/versions/001_initial.py` | Core schema: enums, tables, partial index, FK constraints. Every other component depends on this. |
| S2 | Job creation with idempotency | `services/job_service.py` | UNIQUE constraint handling, race-free INSERT-or-select, event emission — correctness-critical. |
| S3 | Scheduler polling loop | `services/scheduler.py` | `SKIP LOCKED` claim, CLAIMED→RUNNING→SUCCESS/FAILED state machine, retry with exponential backoff. Core execution path. |
| S4 | Job cancellation state machine | `services/job_service.py` (cancel) | Status gating (PENDING only), race with scheduler claiming, 409 for non-cancellable states. |
| S5 | `POST /jobs` router contract | `routers/jobs.py` | Request validation, service orchestration, 201 vs 200 response code, error mapping. Public API edge. |

### Tier: senior-engineer

| # | Task | File(s) |
|---|---|---|
| D1 | Project scaffold | `pyproject.toml`, `src/job_scheduler/__init__.py`, `.gitignore`, `.env.example`, `src/job_scheduler/config.py` |
| D2 | Database engine + session | `src/job_scheduler/database.py` |
| D3 | App factory + lifespan + /healthz | `src/job_scheduler/main.py` |
| D4 | Task model + Pydantic schemas | `src/job_scheduler/models/task.py`, `src/job_scheduler/schemas/task.py` |
| D5 | Job model + Pydantic schemas | `src/job_scheduler/models/job.py`, `src/job_scheduler/schemas/job.py` |
| D6 | Event model + Pydantic schemas | `src/job_scheduler/models/event.py`, `src/job_scheduler/schemas/event.py` |
| D7 | Task service + router | `src/job_scheduler/services/task_service.py`, `src/job_scheduler/routers/tasks.py` |
| D8 | Job status + history endpoints | `src/job_scheduler/routers/jobs.py` (GET endpoints) |
| D9 | Dockerfile + docker-compose.yml | Multi-stage, healthchecks, APP_PORT |
| D10 | White-box tests | `tests/conftest.py`, `tests/test_task_service.py`, `tests/test_job_service.py`, `tests/test_scheduler.py` |
| D11 | Acceptance test conftest | `verify/acceptance/conftest.py`, `verify/manifest.env` |
| D12 | README + DEPLOY.md | Quick-start, API table, deploy steps |
