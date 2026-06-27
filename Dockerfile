# Multi-stage build for Job Scheduler
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

# Runtime stage
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=builder /usr/local/bin/alembic /usr/local/bin/alembic

COPY alembic/ alembic/
COPY alembic.ini .
COPY src/ src/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "job_scheduler.main:app", "--host", "0.0.0.0", "--port", "8000"]
