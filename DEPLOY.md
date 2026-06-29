# Deployment Guide

## Prerequisites

- **Docker** (20.10+) and **Docker Compose** (v2, the `docker compose` plugin)
- **curl** for health checks
- **Python 3.11+** and **pip** (for local development only)
- An available host port (default: `8010`)

> **Port conflicts:** The default `APP_PORT=8010` avoids collisions with common services (5432/Postgres, 6379/Redis, 8000/8080). If 8010 is taken, set `APP_PORT` in `.env` or on the command line.

## Docker Compose (Recommended)

### 1. Start the stack

```bash
docker compose up --build -d
```

This builds the application image, starts PostgreSQL 16, waits for it to become healthy, runs Alembic migrations, and launches the API server.

### 2. Verify the deployment

```bash
# Health check
curl -sf http://localhost:8010/healthz
# Expected: {"status":"ok"}

# Check service status
docker compose ps
```

### 3. Run functional tests

```bash
API_BASE_URL=http://localhost:8010 pytest tests/functional/ -v
```

### 4. Stop the stack

```bash
# Stop containers (preserve data)
docker compose down

# Stop and delete volumes (fresh start)
docker compose down -v
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://scheduler:scheduler@db:5432/scheduler` | Async PostgreSQL connection string. The compose file sets this automatically to point at the `db` service. For local dev, change `db` to `localhost`. |
| `SCHEDULER_POLL_INTERVAL_MS` | `500` | How often the scheduler polls for due jobs (milliseconds). Lower = more responsive but higher DB load. |
| `APP_PORT` | `8010` | Host port mapping for the API. Container always listens on 8000 internally. |

To override, create a `.env` file or pass on the command line:

```bash
# .env file
APP_PORT=9000
SCHEDULER_POLL_INTERVAL_MS=250

# Or inline
APP_PORT=9000 docker compose up -d
```

## Database Migrations

Migrations run automatically on container startup via the compose `command`:

```sh
alembic upgrade head && uvicorn job_scheduler.main:app --host 0.0.0.0 --port 8000
```

The initial migration (`001_initial`) creates:

| Table | Purpose |
|-------|---------|
| `tasks` | Task definitions (name, retry/timeout policy) |
| `jobs` | Job instances with status tracking, scheduling, params |
| `execution_events` | Append-only event log for every state transition |

Plus custom PostgreSQL enum types (`job_status`, `event_type`) and indexes:
- `idx_jobs_status_scheduled` — partial composite index on `(status, scheduled_at)` where `status = 'PENDING'` (the scheduler's main poll query)
- `idx_events_job_time` — composite index on `(job_id, timestamp)` for history queries

### Manual migration (local development)

```bash
pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler
alembic upgrade head
```

### Check current migration state

```bash
# Inside the container
docker compose exec app alembic current

# Or locally
alembic current
```

## Local Development

For development without Docker Compose (requires a running PostgreSQL instance):

```bash
# 1. Install dependencies
pip install -e ".[dev]"

# 2. Set the database URL (point to your local Postgres)
export DATABASE_URL=postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler

# 3. Run migrations
alembic upgrade head

# 4. Start the server with hot reload
uvicorn job_scheduler.main:app --reload --port 8010

# 5. Run tests
pytest tests/unit/ -v                                          # Unit (white-box, needs DB)
API_BASE_URL=http://localhost:8010 pytest tests/functional/ -v  # Functional (black-box, needs running app)
```

## Verifying the Deployment

### Health check

```bash
curl http://localhost:8010/healthz
# {"status":"ok"}
```

### End-to-end smoke test

```bash
# Create a task
TASK_ID=$(curl -sf http://localhost:8010/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke-test","max_retries":2}' | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

echo "Created task: $TASK_ID"

# Schedule an immediate job
JOB_ID=$(curl -sf http://localhost:8010/jobs \
  -H "Content-Type: application/json" \
  -d "{\"task_id\":\"$TASK_ID\",\"params\":{\"fail\":false}}" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

echo "Created job: $JOB_ID"

# Wait for execution and check status
sleep 3
curl -sf http://localhost:8010/jobs/$JOB_ID | python3 -m json.tool

# Check event history
curl -sf http://localhost:8010/jobs/$JOB_ID/history | python3 -m json.tool
```

### Functional suite

```bash
API_BASE_URL=http://localhost:8010 pytest tests/functional/ -v
```

All 9 functional requirements should pass (FR-1 through FR-9).

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs app --tail=100

# Common issues:
# - "connection refused" on DB → Postgres isn't ready yet (check db healthcheck)
# - "relation does not exist" → migrations didn't run (check app startup logs)
```

### Port already in use

```bash
# Find what's using port 8010
lsof -i :8010

# Use a different port
APP_PORT=9000 docker compose up -d
```

### Database connection errors

```bash
# Verify Postgres is running and healthy
docker compose ps db
docker compose exec db pg_isready -U scheduler -d scheduler

# Check the DATABASE_URL the app sees
docker compose exec app env | grep DATABASE_URL
```

### Migrations fail

```bash
# Check current state
docker compose exec app alembic current

# Reset and re-run (DEVELOPMENT ONLY — destroys data)
docker compose down -v
docker compose up -d
```

### Scheduler not picking up jobs

```bash
# Check scheduler logs
docker compose logs app | grep -i scheduler

# Verify poll interval
docker compose exec app env | grep SCHEDULER_POLL_INTERVAL_MS

# Check for PENDING jobs in the database
docker compose exec db psql -U scheduler -d scheduler \
  -c "SELECT job_id, status, scheduled_at FROM jobs WHERE status = 'PENDING' ORDER BY scheduled_at;"
```

### Functional tests timeout

Jobs may take up to 20 seconds to execute (the functional tests poll with a 20s deadline). If tests consistently timeout:
- Check that the scheduler is running (see logs above)
- Verify `SCHEDULER_POLL_INTERVAL_MS` is reasonable (default 500ms)
- Ensure the database is responsive

## Production Considerations

This is a single-process MVP. For production deployment:

- **Separate processes:** Run the scheduler/worker as a dedicated process separate from the API server
- **Horizontal scaling:** Deploy multiple API servers behind a load balancer; run multiple scheduler workers with `FOR UPDATE SKIP LOCKED` for safe concurrent claiming
- **Connection pooling:** Increase `pool_size` and `max_overflow` in `database.py` for high concurrency
- **Monitoring:** Add structured logging, metrics export (Prometheus), and alerting on scheduler lag
- **Graceful shutdown:** The lifespan handler stops the scheduler cleanly; ensure your orchestrator sends SIGTERM with adequate drain time
- **Secrets management:** Replace hardcoded Postgres credentials with secrets injection (Docker secrets, Vault, etc.)
- **Backups:** Configure PostgreSQL WAL archiving and point-in-time recovery
