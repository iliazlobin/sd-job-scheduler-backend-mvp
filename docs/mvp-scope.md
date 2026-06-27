# Job Scheduler MVP — Scope & Acceptance

## 1. Goal & scope

Build a minimal but functional job scheduler: create task definitions, schedule jobs (immediate or at a future time), execute them with at-least-once guarantees, monitor status, and handle retries. The MVP is a single-process scheduler + worker running on PostgreSQL for durable state — no sharding, no Kafka, no timing wheel, no DAG workflows.

**In scope**
- Task definitions with configurable retry/timeout
- Job scheduling: immediate and future-timestamp
- Worker execution loop: claim → run → complete/fail → retry
- Execution event history (append-only log)
- Job cancellation (PENDING jobs)
- Idempotent job creation via client-supplied key

**Out of scope**
- Sharding / multi-scheduler leader election
- Kafka / distributed execution queues
- In-memory timing wheel (use DB polling)
- DAG workflows with parent/child dependencies
- Recurring cron schedules
- Multi-worker fleet (single worker in-process)
- Fence tokens / split-brain protection
- Dead-letter queue

## 2. Functional requirements

**FR-1 — Create task definition.** Client defines a task with name, max_retries, and timeout_sec.
→ `POST /tasks {name, max_retries?, timeout_sec?}` → `201 {task_id, name, …}`; missing name → `422`.

**FR-2 — Schedule immediate job.** Client schedules a job to execute as soon as possible under a task.
→ `POST /jobs {task_id, params?, idempotency_key?}` → `201 {job_id, status: "PENDING", …}`.

**FR-3 — Schedule delayed job.** Client schedules a job to fire at a future UTC timestamp.
→ `POST /jobs {task_id, scheduled_at: "<iso8601>", params?, idempotency_key?}` → `201 {job_id, status: "PENDING", scheduled_at, …}`; `scheduled_at` in the past → `422`.

**FR-4 — Idempotent job creation.** Duplicate `idempotency_key` returns the existing job, not a new one.
→ `POST /jobs {…, idempotency_key: "key-1"}` twice → both return `200` with the same `job_id`; second call does not create a new job.

**FR-5 — Worker executes jobs.** Scheduler polls for PENDING jobs whose `scheduled_at <= NOW()`, claims them (status → RUNNING), and transitions to SUCCESS or FAILED based on outcome.
→ Poll interval ≤1s; job transitions through CLAIMED → RUNNING → SUCCESS/FAILED. Each transition emits an execution_event.

**FR-6 — Query job status.** Client retrieves current state of any job.
→ `GET /jobs/{job_id}` → `200 {job_id, status, scheduled_at, started_at, completed_at, retry_count, …}`; unknown job → `404`.

**FR-7 — Query execution history.** Client retrieves the append-only event log for a job.
→ `GET /jobs/{job_id}/history` → `200 {events: [{event_type, timestamp, payload?}, …]}`; paginated with `?offset=0&limit=20`; unknown job → `404`.

**FR-8 — Cancel pending job.** Client cancels a job that has not yet started.
→ `POST /jobs/{job_id}/cancel` → `200 {job_id, status: "CANCELLED"}`; already RUNNING/SUCCESS/FAILED → `409`; unknown → `404`.

**FR-9 — Retry failed jobs.** Failed jobs are automatically retried up to the task's `max_retries` with exponential backoff (1s, 2s, 4s, 8s, …).
→ Job transitions FAILED → PENDING with `next_retry_at = now + backoff`; after max_retries exceeded → stays FAILED with no further retries.

## 3. Stack & deployment

- **Runtime:** Python 3.12, FastAPI, uvicorn
- **Datastore:** PostgreSQL 16 (jobs, tasks, execution_events)
- **Worker:** Same-process polling loop (separate thread or asyncio task)
- **Tests:** pytest + httpx.ASGITransport (functional), pytest + requests (acceptance, black-box)
- **Deploy:** Docker Compose (app + postgres), port 8010→8000

Design → [System Design: Job Scheduler](https://app.notion.com/p/iliazlobin/38bd865005a881ceb972f8f023db41ec)

## 4. Data model

```
Task
  task_id: UUID (PK)
  name: VARCHAR(255)
  max_retries: INT DEFAULT 3
  timeout_sec: INT DEFAULT 3600
  created_at: TIMESTAMP

Job
  job_id: UUID (PK)
  task_id: UUID (FK → Task)
  status: ENUM(PENDING, CLAIMED, RUNNING, SUCCESS, FAILED, CANCELLED)
  scheduled_at: TIMESTAMP
  started_at: TIMESTAMP
  completed_at: TIMESTAMP
  params: JSONB
  idempotency_key: VARCHAR(255) UNIQUE   ← client-generated; deduplication guard
  retry_count: INT DEFAULT 0
  next_retry_at: TIMESTAMP
  created_at: TIMESTAMP

ExecutionEvent
  event_id: BIGSERIAL (PK)
  job_id: UUID (FK → Job)
  event_type: ENUM(ENQUEUED, CLAIMED, STARTED, COMPLETED, FAILED, RETRYING, CANCELLED)
  timestamp: TIMESTAMP
  payload: JSONB                         ← error message, retry reason
```

## 5. API

- `POST /tasks` — Create a task definition
- `POST /jobs` — Schedule a job (immediate or future)
- `GET /jobs/{job_id}` — Get job status and metadata
- `GET /jobs/{job_id}/history` — Get paginated execution event timeline
- `POST /jobs/{job_id}/cancel` — Cancel a PENDING job

## 6. Test scenarios

- **Idempotency:** same `idempotency_key` twice → same `job_id`, no duplicate
- **Delayed execution:** job with `scheduled_at` in the future stays PENDING until time arrives
- **Past scheduling:** `scheduled_at` in the past → 422
- **Cancellation:** cancel PENDING → CANCELLED; cancel RUNNING → 409
- **Retry loop:** FAILED job auto-retries up to `max_retries` with exponential backoff
- **Event ordering:** execution event history is chronologically ordered
- **Validation:** missing required fields → 422; unknown task_id → 404
- **404 handling:** unknown job_id returns 404 on all GET/POST endpoints

## 7. Module layout

```
sd-job-scheduler-backend-mvp/
├── src/job_scheduler/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory + lifespan + /healthz
│   ├── config.py            # pydantic-settings
│   ├── database.py          # async engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── task.py          # SQLAlchemy Task model
│   │   ├── job.py           # SQLAlchemy Job model
│   │   └── event.py         # SQLAlchemy ExecutionEvent model
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── task.py          # Pydantic request/response
│   │   ├── job.py
│   │   └── event.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── tasks.py         # POST /tasks
│   │   └── jobs.py          # POST/GET /jobs, cancel, history
│   ├── services/
│   │   ├── __init__.py
│   │   ├── task_service.py
│   │   ├── job_service.py
│   │   └── scheduler.py     # background polling loop
│   └── migrations/
│       └── ...              # Alembic migrations
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_task_service.py
│   ├── test_job_service.py
│   └── test_scheduler.py
├── verify/
│   ├── __init__.py
│   └── acceptance/
│       ├── __init__.py
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
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── README.md
├── DEPLOY.md
└── design.md
```

## 8. Run

```bash
# Start
docker compose up -d

# Health check
curl http://localhost:8010/healthz

# Run tests
pytest tests/unit/ -v
pytest tests/functional/ -v
API_BASE_URL=http://localhost:8010 pytest verify/acceptance/ -v

# Stop
docker compose down
```

## 9. Build Plan

Cards listed in dependency order. Each card blocks its dependents (via kanban `parents`).
The verifier gates every build card; on BLOCK the card goes back to its engineer for fixes.

### Phase 0 — Design (this card, t_9bcfa6e3)
- **architect** produces `design.md`, `verify/acceptance/*`, and this Build Plan.
  No app code. Downstream cards have this as parent.

### Phase 1 — Foundation (parallel-ready after Phase 0)

| Card | Title | Assignee | Tier | Depends on |
|------|-------|----------|------|------------|
| C1 | Scaffold: pyproject.toml, config, .gitignore, .env.example, database.py, app factory + /healthz | senior-engineer | senior | Phase 0 |
| C2 | Data model + Alembic migration (tasks, jobs, execution_events, enums, indexes) | staff-engineer | staff | Phase 0 |

### Phase 2 — Business logic (parallel-ready after C1 + C2)

| Card | Title | Assignee | Tier | Depends on |
|------|-------|----------|------|------------|
| C3 | Pydantic schemas: TaskCreate/Response, JobCreate/Response, Event/History, CancelResponse | senior-engineer | senior | C1, C2 |
| C4 | Task service + POST /tasks router | senior-engineer | senior | C1, C2, C3 |
| C5 | Job service — create (with idempotency), get, cancel state machine | staff-engineer | staff | C1, C2, C3 |
| C6 | Scheduler — polling loop (SKIP LOCKED), CLAIMED→RUNNING→SUCCESS/FAILED, retry with exponential backoff | staff-engineer | staff | C1, C2 |

### Phase 3 — API surface (after C4 + C5)

| Card | Title | Assignee | Tier | Depends on |
|------|-------|----------|------|------------|
| C7 | Job router — POST /jobs, GET /jobs/{id}, GET /jobs/{id}/history, POST /jobs/{id}/cancel | staff-engineer | staff | C4, C5 |

### Phase 4 — Infrastructure (parallel-ready after C1)

| Card | Title | Assignee | Tier | Depends on |
|------|-------|----------|------|------------|
| C8 | Dockerfile + docker-compose.yml + verify/manifest.env (host e2e wiring) | senior-engineer | senior | C1 |

### Phase 5 — Tests + Docs (after C7 + C8)

| Card | Title | Assignee | Tier | Depends on |
|------|-------|----------|------|------------|
| C9 | White-box tests: conftest, test_task_service, test_job_service, test_scheduler | senior-engineer | senior | C7 |
| C10 | README.md + DEPLOY.md (evidence-backed, API table, quick-start) | writer | senior | C7, C8 |

### Phase 6 — Verification gates

| Card | Title | Assignee | Tier | Depends on |
|------|-------|----------|------|------------|
| V1 | Verify gate — scaffold + models + schemas (tests pass, /healthz 200, migrations apply) | verifier | senior | C1, C2, C3 |
| V2 | Verify gate — full build (all white-box pass, acceptance suite green against compose) | verifier | senior | C7, C8, C9 |

**Acceptance suite** lives at `verify/acceptance/` (9 files, 1 per FR). The host e2e loop runs it against the live `docker compose` stack. The final build card wires `e2e-verify init → run`; green-is-ship.
