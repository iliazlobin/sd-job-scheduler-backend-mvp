# System Design: Job Scheduler

## 1. Problem frame
A distributed job scheduler accepts work definitions and fans their execution across a fleet of worker machines — on demand, at a future wall-clock time, or on a recurring cron schedule. It tracks dependencies between tasks so a DAG workflow advances only when all upstream steps succeed, and it guarantees each job runs at least once through machine crashes, network partitions, and scheduler failovers. This is infrastructure that powers ETL pipelines at Shopify (10K DAGs, 150K Airflow runs/day), business workflow automation at Uber (12B+ Cadence executions), and async compute at Pinterest (billions of tasks). The system targets 10,000 jobs/sec peak throughput with sub-2-second scheduling precision and no single-process SPOF.

## 2. Requirements

### Functional
- FR1: Schedule jobs — execute immediately, at a future UTC timestamp, or on a recurring cron expression
- FR2: Define DAG workflows — tasks declare upstream parents; downstream jobs fire only after all parents succeed
- FR3: Execute reliably — at-least-once delivery with configurable retry count and exponential backoff
- FR4: Monitor status — query job state, workflow progress, and per-execution event history
- FR5: Cancel jobs — cancel pending jobs, interrupt currently-running ones via worker signal
- FR6: Retrieve execution history — append-only event log of every state transition for audit and debugging

### Non-functional
- NFR1: 10,000 jobs/sec peak throughput — horizontal scaling beyond a single machine
- NFR2: ≤2s scheduling precision — job fires within 2 seconds of its scheduled wall-clock time
- NFR3: 99.9% availability — no single scheduler process is a SPOF; failover is sub-10-second
- NFR4: Durability — zero job loss on crash; every accepted job eventually executes or lands in a dead-letter queue

Out of scope: multi-tenant quota enforcement, resource-aware bin-packing, visual DAG editor.

## 3. Back of the envelope
- **10K jobs/sec peak → scheduler sharding.** A single PostgreSQL instance tops out around 5K writes/sec for the scheduling workload (complex `FOR UPDATE SKIP LOCKED` plus index maintenance). At 10K/sec we must partition the job table by shard and run independent scheduler leaders per shard. Temporal uses 512 fixed shards for the same reason; we adopt consistent-hash sharding so the shard count can be resized online.
- **864M jobs/day → 432 GB raw job data daily, ~13TB for a 30-day window.** Each job record is ~500 bytes (UUIDs, timestamps, JSONB params, status). Prune completed jobs older than 30 days from the hot store; archive to cold object storage (S3/GCS). Execution events are the bigger concern: ~2KB per job execution → 1.7TB/day. Stream them to a time-series store with TTL and keep only the last 7 days hot.
- **≤2s precision → in-memory timing wheel, not full-table DB scan.** A `WHERE next_fire_at <= NOW()` query on a table with 100M+ rows is a range scan on a B-tree index — fast enough for one-off but not at 10K/sec throughput with concurrent `UPDATE` contention. An in-memory hierarchical timing wheel handles the near-future window (next 15 minutes) at O(1) tick cost; the DB is polled with `SKIP LOCKED` only for jobs farther out.

## 4. Entities & API

```
task
  task_id: UUID (PK)
  name: VARCHAR(255)
  schedule_type: ENUM(IMMEDIATE, SCHEDULED, RECURRING)
  cron_expr: VARCHAR(100)           ← NULL unless RECURRING
  max_retries: INT DEFAULT 3
  timeout_sec: INT DEFAULT 3600
  created_at: TIMESTAMP

job
  job_id: UUID (PK)
  task_id: UUID (FK → task)
  shard_id: INT                     ← hash(job_id) % num_shards; partition key
  status: ENUM(PENDING, CLAIMED, RUNNING, SUCCESS, FAILED, CANCELLED)
  scheduled_at: TIMESTAMP
  started_at: TIMESTAMP
  completed_at: TIMESTAMP
  params: JSONB
  idempotency_key: VARCHAR(255) UNIQUE  ← client-generated UUIDv4; deduplication guard
  retry_count: INT DEFAULT 0
  fence_token: BIGINT DEFAULT 0     ← monotonic; storage rejects lower-token writes
  next_retry_at: TIMESTAMP

workflow
  workflow_id: UUID (PK)
  name: VARCHAR(255)
  status: ENUM(PENDING, RUNNING, SUCCESS, FAILED)
  created_at: TIMESTAMP

workflow_task_edge
  workflow_id: UUID (FK → workflow)
  child_task_id: UUID (FK → task)
  parent_task_id: UUID (FK → task)
  pending_parents: INT DEFAULT 0    ← denormalized counter; enqueue when reaches zero
  PRIMARY KEY (workflow_id, child_task_id, parent_task_id)

execution_event
  event_id: BIGSERIAL (PK)
  job_id: UUID (FK → job)
  event_type: ENUM(ENQUEUED, CLAIMED, STARTED, HEARTBEAT, COMPLETED, FAILED, RETRYING, CANCELLED)
  timestamp: TIMESTAMP
  worker_id: VARCHAR(255)
  payload: JSONB                    ← error stack, heartbeat progress, retry reason
```

### API
- `POST /tasks` — Create a task definition, returns `task_id`
- `POST /jobs` — Schedule a job (body carries `schedule_type`, `scheduled_at` or `cron_expr`, `params`, optional `idempotency_key`), returns `job_id`
- `GET /jobs/{job_id}` — Get job status, metadata, and retry count
- `GET /jobs/{job_id}/history` — Get paginated execution event timeline
- `POST /jobs/{job_id}/cancel` — Cancel a PENDING job or signal a RUNNING worker to interrupt
- `POST /workflows` — Define a workflow DAG (list of task definitions + parent edges), returns `workflow_id`
- `GET /workflows/{workflow_id}` — Get workflow status and all constituent job statuses

## 5. High-Level Design

```
                           ┌─────────────────────┐
                           │   API Gateway        │
                           │   stateless, JWT     │
                           └──────────┬──────────┘
                                      │ route by shard_id
                           ┌──────────▼──────────┐
                           │   Shard Manager      │
                           │   consistent-hash    │
                           │   leader election    │
                           └──┬───────┬───────┬──┘
                    ┌─────────▼┐ ┌────▼──┐ ┌──▼─────────┐
                    │Scheduler │ │Sched  │ │Scheduler    │
                    │Leader S1 │ │Lead S2│ │Leader SN    │
                    │+TimingWhl│ │+TW    │ │+TimingWheel │
                    └────┬─────┘ └───┬───┘ └─────┬───────┘
                         │           │            │
              ┌──────────▼──┐  ┌────▼────┐  ┌────▼────────┐
              │PostgreSQL   │  │PG       │  │PostgreSQL    │
              │partition S1 │  │part S2  │  │partition SN  │
              └─────────────┘  └─────────┘  └──────────────┘
                         │           │            │
              ┌──────────▼──┐  ┌────▼────┐  ┌────▼────────┐
              │Kafka Topic  │  │Kafka T2 │  │Kafka Topic N │
              │Partition 1  │  │         │  │              │
              └──────┬──────┘  └────┬────┘  └──────┬───────┘
                     │              │               │
              ┌──────▼──────┐ ┌────▼────┐  ┌───────▼──────┐
              │Worker Pool  │ │Worker   │  │Worker Pool   │
              │consumer grp │ │Pool     │  │consumer grp  │
              └─────────────┘ └─────────┘  └──────────────┘

  Storage: PostgreSQL (partitioned by shard) + Redis ZSET (delayed queue) + time-series DB (events)
  Execution: Kafka (job dispatch) + stateless worker pools (consumer groups)
```

## 6. Key Design Decisions

1. **Shard-aware scheduler leaders.** Each scheduler owns a subset of shards (consistent hashing). The shard manager assigns shards and handles leader election via etcd. A scheduler scans only its own shard's `PENDING` jobs with `FOR UPDATE SKIP LOCKED`.

2. **Two-tier scheduling.** In-memory hierarchical timing wheel for the 15-minute near window (O(1) tick), Redis ZSET for the long tail, PostgreSQL as the durable source of truth. The timing wheel avoids the DB scan hot-path at peak throughput.

3. **Kafka for execution dispatch.** Decouples scheduling from execution. Scheduler publishes to a shard's Kafka partition; workers consume from their assigned partitions. At-least-once delivery with `enable.auto.commit=false` and manual offset commit after job completion.

4. **Fence tokens for idempotency.** Every job write carries a monotonically-increasing `fence_token`. The storage layer rejects writes with a lower token, preventing split-brain duplicate execution during leader failover.

5. **Idempotency keys.** Clients generate a UUIDv4 per submission. The API deduplicates by checking the unique `idempotency_key` before creating the job, returning the existing `job_id` on collision.
