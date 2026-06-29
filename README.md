# Job Scheduler — MVP

[![Lint](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/lint.yml/badge.svg)](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/lint.yml)
[![CI](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/ci.yml)
[![Functional](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/functional.yml/badge.svg)](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/functional.yml)

A minimal distributed job scheduler: define tasks, schedule jobs (immediate or delayed), execute with at-least-once guarantees, automatic retries with exponential backoff, and full execution audit trail. Single-process Python/FastAPI app backed by PostgreSQL.

## Quick Start

```bash
# 1. Start the stack (app + PostgreSQL 16)
docker compose up --build -d

# 2. Wait for healthy, then verify
curl -sf http://localhost:8010/healthz
# → {"status":"ok"}

# 3. Run the acceptance suite (optional)
pip install httpx pytest
API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v

# 4. Stop
docker compose down        # keep data
docker compose down -v     # fresh start
```

The app listens on port **8010** (configurable via `APP_PORT` in `.env`). PostgreSQL runs inside Docker; the app runs Alembic migrations on startup.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  /healthz    │  │  POST /tasks │  │  POST /jobs      │  │
│  │  GET  /jobs  │  │              │  │  GET  /jobs/{id} │  │
│  │  POST cancel │  │              │  │  GET  history    │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                    │             │
│         └────────────┬────┴────────────────────┘             │
│                      │                                      │
│              ┌───────▼────────┐                             │
│              │  SQLAlchemy    │  async session pool         │
│              │  (asyncpg)     │  (pool_size=5, overflow=10) │
│              └───────┬────────┘                             │
│                      │                                      │
│  ┌───────────────────▼──────────────────────────────────┐   │
│  │              Background Scheduler                     │   │
│  │  asyncio loop, polls every 500ms                     │   │
│  │  SELECT ... FOR UPDATE SKIP LOCKED → claim job       │   │
│  │  execute → SUCCESS / FAILED → retry with backoff     │   │
│  └───────────────────┬──────────────────────────────────┘   │
│                      │                                      │
└──────────────────────┼──────────────────────────────────────┘
                       │
               ┌───────▼────────┐
               │  PostgreSQL 16 │
               │  ┌───────────┐ │
               │  │  tasks    │ │
               │  │  jobs     │ │
               │  │  events   │ │
               │  └───────────┘ │
               └────────────────┘
```

**Key design decisions:**
- **Single-process** — scheduler and API share one uvicorn worker. The scheduler runs as an `asyncio.Task` started in the FastAPI lifespan.
- **PostgreSQL as the queue** — `SELECT ... FOR UPDATE SKIP LOCKED` provides atomic job claiming without a separate message broker.
- **Append-only event log** — every state transition writes an `ExecutionEvent`. The event table is the audit source of truth.
- **Exponential backoff** — retries use `2^retry_count` seconds (1s, 2s, 4s, 8s, …). Between retries the job is `PENDING` with `next_retry_at` set.

## How the Scheduler Works

```
Every 500ms:
  1. SELECT job_id FROM jobs
     WHERE status = 'PENDING'
       AND (scheduled_at <= now OR next_retry_at <= now)
     ORDER BY scheduled_at ASC
     FOR UPDATE SKIP LOCKED LIMIT 1

  2. No rows? → sleep and retry next tick.

  3. Got a job → PENDING → CLAIMED → RUNNING (emit events for each).

  4. Execute (simulated: params.fail=true → raise, else succeed).

  5. On success → RUNNING → SUCCESS, emit COMPLETED.
     On failure → emit FAILED.
       If retry_count < max_retries:
         → PENDING, retry_count++, next_retry_at = now + 2^retry_count
         → emit RETRYING
       Else:
         → FAILED (terminal)
```

## API Reference

- `GET /healthz` — liveness probe → `{"status":"ok"}`
- `POST /tasks` — create a task definition → `201 {task_id, name, max_retries, timeout_sec, created_at}`
- `POST /jobs` — schedule a job → `201` (new) or `200` (idempotent duplicate) `{job_id, status, scheduled_at, …}`
- `GET /jobs/{job_id}` — get job status → `200 {job_id, task_id, task_name, status, retry_count, max_retries, next_retry_at, …}` or `404`
- `GET /jobs/{job_id}/history` — paginated event log → `200 {events: [...], total, offset, limit}` or `404`. Query params: `offset` (default 0), `limit` (default 20, max 100).
- `POST /jobs/{job_id}/cancel` — cancel a PENDING job → `200 {job_id, status: "CANCELLED"}`, `409` if not PENDING, `404` if unknown.

**Request bodies:**

```jsonc
// POST /tasks
{"name": "send-email", "max_retries": 3, "timeout_sec": 3600}

// POST /jobs
{"task_id": "<uuid>", "scheduled_at": "2026-06-28T12:00:00Z", "params": {"to": "user@example.com"}, "idempotency_key": "unique-key-123"}
```

**Job lifecycle:**

```
         ┌──────────┐
         │  PENDING  │◄──── retry (backoff)
         └─────┬─────┘        ▲
               │              │
          scheduler      ┌────┴────┐
          claims         │  FAILED  │──► retries left? ──► PENDING
               │         └─────────┘
         ┌─────▼─────┐
         │  CLAIMED   │
         └─────┬─────┘
               │
         ┌─────▼─────┐
         │  RUNNING   │
         └─────┬─────┘
               │
        ┌──────┴──────┐
        ▼             ▼
   ┌─────────┐   ┌─────────┐
   │ SUCCESS  │   │  FAILED  │ (terminal, no retries left)
   └─────────┘   └─────────┘

   PENDING ──► CANCELLED  (client-initiated, only from PENDING)
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://scheduler:scheduler@db:5432/scheduler` | Async PostgreSQL connection string |
| `SCHEDULER_POLL_INTERVAL_MS` | `500` | Scheduler poll interval (ms). Lower = more responsive, higher DB load. |
| `APP_PORT` | `8010` | Host port (container always listens on 8000) |

Override via `.env` file or `docker compose -e VAR=value`. See `.env.example`.

## Project Layout

```
├── src/job_scheduler/
│   ├── main.py              # FastAPI app factory, lifespan, /healthz
│   ├── config.py            # pydantic-settings (env vars)
│   ├── database.py          # async engine, session factory, Base
│   ├── models/
│   │   ├── task.py          # Task ORM model
│   │   ├── job.py           # Job ORM model + JobStatus enum
│   │   └── event.py         # ExecutionEvent ORM model + EventType enum
│   ├── schemas/
│   │   ├── task.py          # TaskCreate, TaskResponse
│   │   ├── job.py           # JobCreate, JobResponse, JobStatusResponse, CancelResponse
│   │   └── event.py         # EventResponse, HistoryResponse
│   ├── routers/
│   │   ├── tasks.py         # POST /tasks
│   │   └── jobs.py          # POST /jobs, GET /jobs/{id}, GET history, POST cancel
│   └── services/
│       ├── task_service.py  # create_task, get_task
│       ├── job_service.py   # create_job (idempotent), get_job, cancel_job, get_history
│       └── scheduler.py     # background polling loop, claim → execute → retry
├── alembic/
│   ├── env.py               # async Alembic config
│   └── versions/
│       └── 001_initial.py   # tables + enums + indexes
├── tests/                   # white-box unit/integration tests (need DB)
│   ├── test_task_service.py
│   ├── test_job_service.py
│   ├── test_scheduler.py
│   └── test_schemas.py
├── verify/
│   ├── manifest.env         # e2e-verify config (MODE, PORT, UP, DOWN, READY, ACCEPTANCE)
│   └── acceptance/          # black-box acceptance tests (need running app)
│       ├── conftest.py      # fixtures: client, task_factory
│       ├── test_fr1_create_task.py
│       ├── test_fr2_immediate_job.py
│       ├── test_fr3_delayed_job.py
│       ├── test_fr4_idempotency.py
│       ├── test_fr5_execution.py
│       ├── test_fr6_job_status.py
│       ├── test_fr7_history.py
│       ├── test_fr8_cancel.py
│       └── test_fr9_retry.py
├── .github/workflows/
│   ├── ci.yml               # full pipeline: lint + unit + compose + acceptance + teardown
│   ├── functional.yml       # functional tests only
│   └── lint.yml             # ruff lint
├── docker-compose.yml       # app + postgres:16-alpine
├── Dockerfile               # multi-stage Python 3.12-slim
├── pyproject.toml           # deps, pytest config, ruff config
├── alembic.ini
├── .env.example
├── DESIGN.md                # architecture, data model, API, FR <-> acceptance-test map
├── DEPLOY.md                # deployment guide
└── README.md
```

## Testing

**White-box tests** (need a PostgreSQL database):

```bash
pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler
pytest tests/ -v
```

**Black-box acceptance tests** (need a running app):

```bash
docker compose up --build -d
API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v
docker compose down -v
```

Each `test_fr*.py` file maps to one functional requirement (see the FR table in `DESIGN.md`):

| File | FR | What it verifies |
|------|----|-----------------|
| `test_fr1_create_task.py` | FR-1 | Task creation, validation (422 on missing name) |
| `test_fr2_immediate_job.py` | FR-2 | Immediate job scheduling, 201 + PENDING |
| `test_fr3_delayed_job.py` | FR-3 | Future scheduling, 422 on past timestamp |
| `test_fr4_idempotency.py` | FR-4 | Duplicate idempotency_key → 200 + same job_id |
| `test_fr5_execution.py` | FR-5 | Scheduler picks up job, transitions through CLAIMED → RUNNING → SUCCESS/FAILED |
| `test_fr6_job_status.py` | FR-6 | GET /jobs/{id} returns full state, 404 on unknown |
| `test_fr7_history.py` | FR-7 | Paginated event log, correct event sequence, 404 on unknown |
| `test_fr8_cancel.py` | FR-8 | Cancel PENDING → 200 CANCELLED; RUNNING → 409; unknown → 404 |
| `test_fr9_retry.py` | FR-9 | Exponential backoff retries, terminal FAILED after max_retries exhausted |

## Limitations (MVP scope)

- **Single scheduler process** — no leader election, no horizontal scaling. One crash = all in-flight jobs stall until restart.
- **Simulated execution** — the worker doesn't call external services. `params.fail=true` triggers a failure; anything else succeeds.
- **No auth** — API is open. Add middleware before exposing externally.
- **No dead-letter queue** — terminally failed jobs stay in `FAILED` with no automated alerting.
- **No recurring schedules** — one-shot jobs only. No cron expressions.
- **No multi-worker fleet** — single in-process worker. `SKIP LOCKED` is ready for future multi-instance but not exercised.
