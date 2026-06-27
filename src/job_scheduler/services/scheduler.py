"""Background scheduler — polls for PENDING jobs, claims, executes, retries.

Implements the polling loop described in FR-5 and FR-9:
  - Polls every ≤1s using FOR UPDATE SKIP LOCKED for safe concurrent claiming.
  - State machine: PENDING → CLAIMED → RUNNING → SUCCESS | FAILED.
  - On failure with retries remaining: FAILED → PENDING with exponential backoff.
  - Every state transition emits an ExecutionEvent for auditability.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from job_scheduler.database import async_session_factory
from job_scheduler.config import settings

logger = logging.getLogger(__name__)

# ── Raw SQL statements ────────────────────────────────────────────────
# Using raw SQL for the critical path to get FOR UPDATE SKIP LOCKED,
# which SQLAlchemy ORM doesn't support cleanly with async.

_SQL_CLAIM = text("""
    SELECT job_id FROM jobs
    WHERE status = 'PENDING'
      AND (
        (next_retry_at IS NULL AND scheduled_at <= :now)
        OR (next_retry_at IS NOT NULL AND next_retry_at <= :now)
      )
    ORDER BY scheduled_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
""")

_SQL_SET_CLAIMED = text("""
    UPDATE jobs SET status = 'CLAIMED'
    WHERE job_id = :job_id AND status = 'PENDING'
""")

_SQL_SET_RUNNING = text("""
    UPDATE jobs SET status = 'RUNNING',
        started_at = COALESCE(started_at, :now)
    WHERE job_id = :job_id
""")

_SQL_SET_SUCCESS = text("""
    UPDATE jobs SET status = 'SUCCESS', completed_at = :now
    WHERE job_id = :job_id
""")

_SQL_SET_RETRY = text("""
    UPDATE jobs SET status = 'PENDING',
        retry_count = retry_count + 1,
        next_retry_at = :next_retry
    WHERE job_id = :job_id
""")

_SQL_SET_FAILED = text("""
    UPDATE jobs SET status = 'FAILED', completed_at = :now
    WHERE job_id = :job_id
""")

_SQL_INSERT_EVENT = text("""
    INSERT INTO execution_events (job_id, event_type, timestamp, payload)
    VALUES (:job_id, :event_type, :now, :payload)
""")

_SQL_FETCH_JOB_FOR_EXEC = text("""
    SELECT j.params, j.retry_count, t.max_retries
    FROM jobs j JOIN tasks t ON j.task_id = t.task_id
    WHERE j.job_id = :job_id
""")


class Scheduler:
    """Single-instance background polling loop.

    Started in app lifespan; polls for PENDING jobs whose fire time has
    arrived, claims them atomically via SKIP LOCKED, executes (simulated),
    and handles success/failure/retry transitions.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running: bool = False

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Scheduler started (poll_interval=%dms)",
            settings.scheduler_poll_interval_ms,
        )

    async def stop(self) -> None:
        """Gracefully stop the polling loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Scheduler stopped")

    # ── Internal loop ─────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Main polling loop — runs until stopped."""
        interval = settings.scheduler_poll_interval_ms / 1000.0
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Scheduler tick error")
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        """Single poll iteration: claim one job and execute it."""
        async with async_session_factory() as session:
            await self._claim_and_execute(session)

    async def _claim_and_execute(self, session: AsyncSession) -> None:
        """Claim one PENDING job and execute it through its lifecycle."""
        now = datetime.now(timezone.utc)

        # 1. Find a PENDING job whose fire time has arrived.
        #    FOR UPDATE SKIP LOCKED prevents double-claiming by concurrent
        #    scheduler instances (only one per process in MVP, but the
        #    pattern is correct for future multi-instance deployment).
        result = await session.execute(_SQL_CLAIM, {"now": now})
        row = result.first()
        if row is None:
            return

        job_id = row[0]

        # 2. Atomically transition PENDING → CLAIMED.
        #    The WHERE clause re-checks status to handle the (unlikely)
        #    race where another path changed it between SELECT and UPDATE.
        claim_result = await session.execute(_SQL_SET_CLAIMED, {"job_id": job_id})
        if claim_result.rowcount == 0:
            return  # Lost the race

        # 3. Emit CLAIMED event.
        await session.execute(
            _SQL_INSERT_EVENT,
            {"job_id": job_id, "event_type": "CLAIMED", "now": now, "payload": "{}"},
        )

        # 4. Transition CLAIMED → RUNNING.
        await session.execute(_SQL_SET_RUNNING, {"job_id": job_id, "now": now})

        # 5. Emit STARTED event.
        await session.execute(
            _SQL_INSERT_EVENT,
            {"job_id": job_id, "event_type": "STARTED", "now": now, "payload": "{}"},
        )

        await session.commit()

        # 6. Fetch job params + task retry policy for execution.
        job_result = await session.execute(_SQL_FETCH_JOB_FOR_EXEC, {"job_id": job_id})
        job_row = job_result.first()
        if job_row is None:
            return

        job_params: dict = job_row[0] if isinstance(job_row[0], dict) else {}
        retry_count: int = job_row[1]
        max_retries: int = job_row[2]

        # 7. Execute the job (simulated).
        should_fail = bool(job_params.get("fail", False))

        try:
            await asyncio.sleep(0.01)  # Simulated work

            if should_fail:
                raise RuntimeError(job_params.get("error_message", "simulated failure"))
            else:
                await self._handle_success(session, job_id)

        except Exception as exc:
            await self._handle_failure(session, job_id, retry_count, max_retries, error=str(exc))

    # ── Outcome handlers ──────────────────────────────────────────────

    async def _handle_success(self, session: AsyncSession, job_id) -> None:
        """Transition job to SUCCESS and emit COMPLETED event."""
        now = datetime.now(timezone.utc)

        await session.execute(_SQL_SET_SUCCESS, {"job_id": job_id, "now": now})
        await session.execute(
            _SQL_INSERT_EVENT,
            {"job_id": job_id, "event_type": "COMPLETED", "now": now, "payload": "{}"},
        )
        await session.commit()
        logger.info("Job %s completed successfully", job_id)

    async def _handle_failure(
        self,
        session: AsyncSession,
        job_id,
        retry_count: int,
        max_retries: int,
        error: str = "",
    ) -> None:
        """Handle a failed job execution.

        Always emits a FAILED event (audit trail for every attempt).
        If retries remain, additionally emits RETRYING and resets to PENDING
        with exponential backoff. Otherwise marks terminal FAILED.
        """
        now = datetime.now(timezone.utc)
        failed_payload = json.dumps({"error": error} if error else {})

        # Every failure gets a FAILED event — this is the audit trail.
        await session.execute(
            _SQL_INSERT_EVENT,
            {
                "job_id": job_id,
                "event_type": "FAILED",
                "now": now,
                "payload": failed_payload,
            },
        )

        if retry_count < max_retries:
            # Exponential backoff: 2^retry_count seconds
            backoff_sec = 2**retry_count
            next_retry = now + timedelta(seconds=backoff_sec)

            await session.execute(
                _SQL_SET_RETRY,
                {"job_id": job_id, "next_retry": next_retry},
            )

            retry_payload = json.dumps({"retry_count": retry_count + 1, "backoff_sec": backoff_sec})
            await session.execute(
                _SQL_INSERT_EVENT,
                {
                    "job_id": job_id,
                    "event_type": "RETRYING",
                    "now": now,
                    "payload": retry_payload,
                },
            )
            await session.commit()
            logger.info(
                "Job %s failed, retrying (%d/%d) in %ds",
                job_id,
                retry_count + 1,
                max_retries,
                backoff_sec,
            )
        else:
            # Terminal failure — no retries left.
            await session.execute(_SQL_SET_FAILED, {"job_id": job_id, "now": now})
            await session.commit()
            logger.info(
                "Job %s failed terminally after %d attempts",
                job_id,
                retry_count + 1,
            )
