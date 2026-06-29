# Job Scheduler MVP — Design

A minimal distributed job scheduler: define tasks, schedule jobs (immediate or delayed), execute
them with at-least-once guarantees, retry failures with exponential backoff, and keep a full
execution audit trail. Single-process FastAPI app backed by PostgreSQL.

This document is the build's design of record: architecture, key decisions, data model, API, and the
functional-requirement → functional-test contract. It is MVP-scoped — the broader target (a sharded,
broker-backed scheduler fleet) is summarized inline under [Scope](#scope--limitations); the MVP
deliberately stops short of it.

## 1. Architecture

A single uvicorn process serves the HTTP API **and** runs a background `asyncio` scheduler loop that
claims and executes due jobs. No sharding, no message broker, no in-memory timing wheel — the MVP is
vertically scaled and DB-polled.

```
                    POST/GET
Client ─────────────────────► FastAPI (uvicorn)
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              routers/        lifespan        routers/
            (tasks/jobs)   background loop   (jobs/cancel
                    │         scheduler        /history)
                    ▼              ▼              ▼
              services/      services/      services/
            task_service  scheduler loop  job_service
                    └──────────────┼──────────────┘
                                   ▼
                            PostgreSQL 16
                     ┌──────────┬──────┬──────────────┐
                     │  tasks   │ jobs │ execution_   │
                     │          │      │ events       │
                     └──────────┴──────┴──────────────┘
```

The scheduler runs as an `asyncio.Task` started in the FastAPI lifespan. Every poll interval
(default 500 ms) it claims at most one due job and drives it through its state machine:

```
Every poll interval (≤1s):
  1. SELECT job_id FROM jobs
     WHERE status = 'PENDING'
       AND (scheduled_at <= now OR next_retry_at <= now)
     ORDER BY scheduled_at ASC
     FOR UPDATE SKIP LOCKED LIMIT 1          -- atomic claim, no broker
  2. No rows → sleep, retry next tick.
  3. Got one → PENDING → CLAIMED → RUNNING (an event per transition).
  4. Execute (simulated: params.fail=true → fail, else succeed).
  5. success → RUNNING → SUCCESS, emit COMPLETED.
     failure → emit FAILED; if retry_count < max_retries:
                 → PENDING, retry_count++, next_retry_at = now + 2^retry_count s, emit RETRYING
               else → FAILED (terminal).
```

## 2. Key design decisions

1. **Single-process scheduler + worker.** The background loop executes in the same process as the API.
   Tradeoff: no horizontal scaling, but zero operational complexity for the MVP. The full design moves
   to a separate worker fleet behind a broker.
2. **PostgreSQL as the queue.** `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` claims one job per tick
   atomically, with no separate message broker — correct under a single claimer and already safe for
   multiple instances when the MVP later scales out.
3. **`CLAIMED` intermediate state.** `PENDING → CLAIMED → RUNNING` closes the race where two concurrent
   selects pick the same job: the claim is an atomic `UPDATE … WHERE job_id=? AND status='PENDING'`; if
   zero rows are affected, another claimant already took it.
4. **Idempotency via a `UNIQUE` constraint.** `jobs.idempotency_key` is `UNIQUE`. A duplicate `INSERT`
   raises an integrity error; the service catches it and returns the existing job — no application lock,
   the database guarantees uniqueness.
5. **Exponential backoff retries.** On failure, while `retry_count < task.max_retries`, the job goes back
   to `PENDING` with `next_retry_at = now + 2^retry_count` seconds (1s, 2s, 4s, 8s, …) and a `RETRYING`
   event; once retries are exhausted it stays `FAILED`.
6. **Partial index on `PENDING` jobs.** `CREATE INDEX … WHERE status = 'PENDING'` keeps the scheduler's
   poll query lean regardless of how much terminal job history accumulates.

## 3. Data model

```
Task
  task_id: uuid (PK)
  name: varchar(255)
  max_retries: int            ← default 3
  timeout_sec: int            ← default 3600
  created_at: timestamptz

Job
  job_id: uuid (PK)
  task_id: uuid (FK → Task)
  status: enum(PENDING, CLAIMED, RUNNING, SUCCESS, FAILED, CANCELLED)
  scheduled_at: timestamptz
  started_at: timestamptz
  completed_at: timestamptz
  params: jsonb
  idempotency_key: varchar(255)  ← UNIQUE; client-generated dedup guard
  retry_count: int            ← default 0
  next_retry_at: timestamptz  ← set only while a failed job awaits its next attempt
  created_at: timestamptz
  INDEX (status, scheduled_at) WHERE status = 'PENDING'   ← scheduler poll index

ExecutionEvent                ← append-only audit log; the source of truth for history
  event_id: bigserial (PK)
  job_id: uuid (FK → Job)
  event_type: enum(ENQUEUED, CLAIMED, STARTED, COMPLETED, FAILED, RETRYING, CANCELLED)
  timestamp: timestamptz
  payload: jsonb              ← error message / retry reason
```

## 4. API

- `GET /healthz` — liveness/readiness probe → `{"status":"ok"}`.
- `POST /tasks` — create a task definition (`name`, optional `max_retries`, `timeout_sec`).
- `POST /jobs` — schedule a job under a task: immediate (omit `scheduled_at`) or delayed (future ISO-8601
  `scheduled_at`); optional `idempotency_key`. Returns `201` for a new job, `200` for an idempotent match.
- `GET /jobs/{job_id}` — current job state + task metadata.
- `GET /jobs/{job_id}/history` — paginated, chronologically ordered execution-event timeline
  (`?offset=0&limit=20`, `limit` max 100).
- `POST /jobs/{job_id}/cancel` — cancel a `PENDING` job; no effect on started or terminal jobs.

Job lifecycle:

```
   PENDING ◄──── retry (backoff) ──── FAILED (retries left)
      │ claim
   CLAIMED
      │
   RUNNING ──► SUCCESS
      │     └► FAILED (terminal, no retries left)
   PENDING ──► CANCELLED   (client-initiated, only from PENDING)
```

## 5. Functional requirements ↔ functional tests

Each FR is proven by exactly one black-box functional test in `tests/functional/` that drives the
running service over HTTP (no app imports):

| FR | Behaviour | Functional test |
|----|-----------|-----------------|
| FR-1 | Create a task definition; missing `name` → `422` | `tests/functional/test_fr1_create_task.py` |
| FR-2 | Schedule an immediate job → `201 PENDING` | `tests/functional/test_fr2_immediate_job.py` |
| FR-3 | Schedule a delayed job at a future timestamp; past `scheduled_at` → `422` | `tests/functional/test_fr3_delayed_job.py` |
| FR-4 | Duplicate `idempotency_key` → `200` with the same `job_id`, no new row | `tests/functional/test_fr4_idempotency.py` |
| FR-5 | Scheduler claims a due job and runs `CLAIMED → RUNNING → SUCCESS/FAILED`, emitting an event per transition | `tests/functional/test_fr5_execution.py` |
| FR-6 | `GET /jobs/{id}` returns full state; unknown id → `404` | `tests/functional/test_fr6_job_status.py` |
| FR-7 | `GET /jobs/{id}/history` returns the paginated, ordered event log; unknown id → `404` | `tests/functional/test_fr7_history.py` |
| FR-8 | Cancel `PENDING` → `200 CANCELLED`; non-pending → `409`; unknown → `404` | `tests/functional/test_fr8_cancel.py` |
| FR-9 | Failed jobs auto-retry with exponential backoff up to `max_retries`, then stay `FAILED` | `tests/functional/test_fr9_retry.py` |

## 6. Test scenarios

The unit suite under `tests/unit/` covers the important behaviours and edge cases beyond the per-FR
functional contract:

- **Idempotency** — same `idempotency_key` twice yields the same `job_id`, no duplicate row.
- **Delayed execution** — a job with a future `scheduled_at` stays `PENDING` until its time arrives.
- **Past scheduling** — `scheduled_at` in the past → `422`.
- **Cancellation gating** — cancel `PENDING` → `CANCELLED`; cancel `RUNNING`/terminal → `409`.
- **Retry loop** — a failing job retries up to `max_retries` with exponential backoff, then stays `FAILED`.
- **Event ordering** — the execution-event history is chronologically ordered.
- **Validation** — missing required fields → `422`; unknown `task_id` → `404`.
- **404 handling** — an unknown `job_id` returns `404` on every GET/POST endpoint.

## 7. Test results

Three suites gate every push and run daily on a schedule; the live badges and Actions runs are the
source of truth (no point-in-time logs are pasted here):

[![Lint](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/lint.yml/badge.svg)](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/lint.yml)
[![CI](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/ci.yml)
[![Functional](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/functional.yml/badge.svg)](https://github.com/iliazlobin/sd-job-scheduler-backend-mvp/actions/workflows/functional.yml)

- **Lint** (`.github/workflows/lint.yml`) — `ruff check`.
- **CI** (`.github/workflows/ci.yml`) — the unit suite (`tests/unit/`) against PostgreSQL, then the
  black-box functional suite (`tests/functional/`, all nine FRs) against the running Compose stack.
- **Functional** (`.github/workflows/functional.yml`) — the functional suite (`tests/functional/`,
  FR-1…FR-9) on its own against the running Compose stack.

The §6 Test scenarios above map to the unit suite (`tests/unit/`); the §5 FR table maps to the
functional suite (`tests/functional/`). CI re-runs both on every push and on a daily schedule, so this
section stays live rather than reflecting a single run.

## Scope & limitations

This MVP intentionally stops at a single-process, DB-polled scheduler. The broader target design adds a
separate worker fleet behind a message broker, leader election / sharding across scheduler instances, a
timing wheel for high-fanout scheduling, recurring (cron) schedules, DAG workflows, and a dead-letter
queue — all **out of scope** here. Concretely, the MVP does **not** include:

- **Horizontal scaling / leader election** — one scheduler process; a crash stalls in-flight jobs until
  restart (`SKIP LOCKED` is multi-instance-safe but not exercised here).
- **Real execution** — the worker simulates work; `params.fail=true` forces a failure, anything else
  succeeds.
- **Auth** — the API is open; add middleware before external exposure.
- **Recurring schedules** — one-shot jobs only, no cron expressions.
- **Dead-letter queue** — terminally failed jobs remain `FAILED` with no automated alerting.
