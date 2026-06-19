# Sub-plan A: PostgreSQL + dag_flows

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace aiosqlite/SQLite with asyncpg/PostgreSQL; introduce `dag_flows` table with 3 preset flows; preserve all existing public `api.db` function signatures so callers need no changes.

**Architecture:** asyncpg connection pool created in FastAPI lifespan and stored in `app.state.pool`. All query functions receive the pool as first argument. `api/db/__init__.py` re-exports a thin shim that pulls the pool from the running app — callers stay identical to today.

**Tech Stack:** asyncpg 0.30+, PostgreSQL 16 (Docker), pytest-asyncio, unittest.mock

## Global Constraints

- Python ≥ 3.11
- Public API of `api.db` must not change (same function names, same signatures) — callers in `api/routes/`, `api/event_processor.py`, etc. are untouched until later sub-plans
- PostgreSQL DSN from env `DATABASE_URL`; default `postgresql://mediaflow:changeme@localhost:5432/mediaflow`
- All timestamps stored as REAL (Unix epoch seconds) matching existing SQLite columns
- `api/db.py` is kept and re-routed to the new asyncpg implementation — deleted only after all callers confirmed working
- Commit after every task

---

## File Structure

```
# Create
api/db/
  __init__.py            — re-export all public functions; pool accessor shim
  queries.py             — asyncpg implementations of every public function
  migrations/
    001_init.sql         — CREATE TABLE jobs, dag_flows, events + indexes
    002_seed_flows.sql   — INSERT 3 preset dag_flows rows

# Modify
docker-compose.yml       — add postgres service + healthcheck
requirements.txt         — add asyncpg==0.30.0; keep aiosqlite until done
api/main.py              — init_pool() in lifespan; store in app.state.pool
config.yaml.example      — add postgres section

# Keep (shim — not deleted yet)
api/db.py                — replace body with: from api.db import *  (re-export)

# Test
tests/test_db_queries.py — unit tests for all query functions (mock asyncpg pool)
```

---

## Interfaces

**Produces** (these exact signatures, consumed by C, D, E, F):

```python
# api/db/queries.py — every function takes pool: asyncpg.Pool as first arg
# api/db/__init__.py shim calls these with app.state.pool automatically

async def init(pool: asyncpg.Pool) -> None: ...
async def upsert_job(pool, job_id: str, **kwargs) -> None: ...
async def insert_event(pool, job_id: str, stage: str, status: str, **kwargs) -> None: ...
async def get_job(pool, job_id: str) -> dict | None: ...
async def get_status_overview(pool) -> dict: ...   # keys: processing, queue, recent, failed
async def count_active_jobs(pool) -> int: ...
async def get_dag_flow(pool, flow_id: str | None) -> dict: ...  # None → default flow
async def get_task_aggregates(pool) -> dict: ...   # total_tasks, total_duration_sec, completed
async def get_stage_events(pool, job_id: str) -> list[dict]: ...

# Legacy shims (same names as current api/db.py — no callers change)
async def upsert_task(stem: str, **kwargs) -> None: ...   # maps stem→job_id
async def get_task(stem: str) -> dict | None: ...
# etc.
```

---

## Task 1: PostgreSQL in Docker Compose

**Files:**
- Modify: `docker-compose.yml`
- Modify: `config.yaml.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Add postgres service to docker-compose.yml**

```yaml
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: mediaflow
      POSTGRES_USER: mediaflow
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mediaflow -d mediaflow"]
      interval: 5s
      timeout: 3s
      retries: 10
```

Add `postgres-data:` under `volumes:`. Add `postgres: condition: service_healthy` to `api.depends_on`.

- [ ] **Step 2: Add postgres env to api service in docker-compose.yml**

```yaml
      - DATABASE_URL=postgresql://mediaflow:${POSTGRES_PASSWORD:-changeme}@postgres:5432/mediaflow
```

- [ ] **Step 3: Add postgres section to config.yaml.example**

```yaml
postgres:
  host: localhost
  port: 5432
  database: mediaflow
  user: mediaflow
  password: changeme
  # Or set DATABASE_URL env var to override all fields above
```

- [ ] **Step 4: Add asyncpg to requirements.txt**

```
asyncpg==0.30.0
```

- [ ] **Step 5: Start postgres and verify**

```bash
docker compose up -d postgres
docker compose exec postgres psql -U mediaflow -c "\l"
```

Expected: `mediaflow` database listed.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml config.yaml.example requirements.txt
git commit -m "feat(infra): add PostgreSQL 16 service to Docker Compose"
```

---

## Task 2: SQL Migrations

**Files:**
- Create: `api/db/migrations/001_init.sql`
- Create: `api/db/migrations/002_seed_flows.sql`

- [ ] **Step 1: Write 001_init.sql**

```sql
-- api/db/migrations/001_init.sql
CREATE TABLE IF NOT EXISTS dag_flows (
    id          TEXT    PRIMARY KEY,
    stage_plan  JSONB   NOT NULL,
    is_default  BOOLEAN DEFAULT false,
    deprecated  BOOLEAN DEFAULT false,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT    PRIMARY KEY,
    filename            TEXT    NOT NULL,
    submitted_by        TEXT    NOT NULL DEFAULT 'anonymous',
    dag_flow_id         TEXT    REFERENCES dag_flows(id),
    status              TEXT    NOT NULL DEFAULT 'submitted'
                        CHECK(status IN ('submitted','queued','processing','completed','failed')),
    current_stage       TEXT,
    submitted_at        REAL,
    started_at          REAL,
    completed_at        REAL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    error_msg           TEXT,
    output_srt_path     TEXT,
    corrected_srt_path  TEXT,
    verification_status TEXT    NOT NULL DEFAULT 'unverified'
                        CHECK(verification_status IN ('unverified','in_progress','verified')),
    verified_at         REAL,
    verified_by         TEXT,
    minio_input_key     TEXT,
    minio_processing_key TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id             SERIAL  PRIMARY KEY,
    job_id         TEXT    NOT NULL REFERENCES jobs(id),
    stage          TEXT    NOT NULL,
    status         TEXT    CHECK(status IN ('started','success','failed')),
    retry_attempt  INTEGER NOT NULL DEFAULT 0,
    error_msg      TEXT,
    payload        TEXT,
    ts             REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status    ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_events_job_id  ON events(job_id);
```

- [ ] **Step 2: Write 002_seed_flows.sql**

```sql
-- api/db/migrations/002_seed_flows.sql
INSERT INTO dag_flows (id, stage_plan, is_default, deprecated, created_at)
VALUES
(
  'general-v1',
  '[
    {"stage":"preprocess","config":{"provider":"ffmpeg"}},
    {"stage":"transcribe","config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"summarize","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"summarize","recording_type":"general"}}
  ]'::jsonb,
  true, false, extract(epoch from now())
),
(
  'course-v1',
  '[
    {"stage":"preprocess","config":{"provider":"ffmpeg"}},
    {"stage":"transcribe","config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"verify_segments","config":{"provider":"mlx-whisper","language":"zh","model":"large-v3"}},
    {"stage":"diarize","config":{"provider":"speechbrain","num_speakers":null,"speaker_format":"【{label}】"}},
    {"stage":"correct_srt","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"correct_srt"}},
    {"stage":"summarize","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"summarize","recording_type":"course"}},
    {"stage":"detect_chapters","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"detect_chapters","min_gap_sec":30}}
  ]'::jsonb,
  false, false, extract(epoch from now())
),
(
  'meeting-v1',
  '[
    {"stage":"preprocess","config":{"provider":"ffmpeg"}},
    {"stage":"transcribe","config":{"provider":"mlx-whisper","language":"zh","model":"medium"}},
    {"stage":"diarize","config":{"provider":"speechbrain","num_speakers":null,"speaker_format":"【{label}】"}},
    {"stage":"summarize","config":{"provider":"ollama","model":"qwen2.5:7b","prompt_key":"summarize","recording_type":"meeting"}}
  ]'::jsonb,
  false, false, extract(epoch from now())
)
ON CONFLICT (id) DO NOTHING;
```

- [ ] **Step 3: Run migrations against docker postgres**

```bash
docker compose exec -T postgres psql -U mediaflow -d mediaflow < api/db/migrations/001_init.sql
docker compose exec -T postgres psql -U mediaflow -d mediaflow < api/db/migrations/002_seed_flows.sql
```

Expected: `CREATE TABLE`, `CREATE INDEX`, `INSERT 0 3` (or `INSERT 3 0` — no conflict on first run).

- [ ] **Step 4: Verify schema**

```bash
docker compose exec postgres psql -U mediaflow -d mediaflow -c "\dt"
docker compose exec postgres psql -U mediaflow -d mediaflow -c "SELECT id, is_default FROM dag_flows;"
```

Expected: 3 tables listed; 3 rows in dag_flows; general-v1 has `is_default = t`.

- [ ] **Step 5: Commit**

```bash
git add api/db/migrations/
git commit -m "feat(db): add PostgreSQL schema — jobs, dag_flows, events + seed flows"
```

---

## Task 3: asyncpg Query Module

**Files:**
- Create: `api/db/__init__.py`
- Create: `api/db/queries.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_db_queries.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import time

# We test queries.py by injecting a mock pool
# pool.fetchrow(), pool.execute(), pool.fetch() are AsyncMock

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
    return pool

@pytest.mark.asyncio
async def test_upsert_job_inserts_new(mock_pool):
    from api.db.queries import upsert_job
    mock_pool.execute.return_value = None
    await upsert_job(mock_pool, "job1", filename="test.m4a", status="submitted")
    assert mock_pool.execute.called
    sql, *args = mock_pool.execute.call_args[0]
    assert "INSERT INTO jobs" in sql
    assert "job1" in args

@pytest.mark.asyncio
async def test_get_job_returns_dict(mock_pool):
    from api.db.queries import get_job
    mock_pool.fetchrow.return_value = {
        "id": "job1", "filename": "test.m4a", "status": "completed",
        "submitted_by": "anonymous", "dag_flow_id": "general-v1",
        "current_stage": None, "submitted_at": 1000.0, "started_at": 1001.0,
        "completed_at": 1010.0, "retry_count": 0, "error_msg": None,
        "output_srt_path": None, "corrected_srt_path": None,
        "verification_status": "unverified", "verified_at": None,
        "verified_by": None, "minio_input_key": None, "minio_processing_key": None,
    }
    result = await get_job(mock_pool, "job1")
    assert result["id"] == "job1"
    assert result["status"] == "completed"

@pytest.mark.asyncio
async def test_get_job_not_found_returns_none(mock_pool):
    from api.db.queries import get_job
    mock_pool.fetchrow.return_value = None
    result = await get_job(mock_pool, "nonexistent")
    assert result is None

@pytest.mark.asyncio
async def test_count_active_jobs(mock_pool):
    from api.db.queries import count_active_jobs
    mock_pool.fetchrow.return_value = {"count": 3}
    result = await count_active_jobs(mock_pool)
    assert result == 3

@pytest.mark.asyncio
async def test_insert_event(mock_pool):
    from api.db.queries import insert_event
    await insert_event(mock_pool, "job1", stage="transcribe", status="success",
                       retry_attempt=0, ts=time.time())
    assert mock_pool.execute.called
    sql, *_ = mock_pool.execute.call_args[0]
    assert "INSERT INTO events" in sql

@pytest.mark.asyncio
async def test_get_dag_flow_returns_default_when_none(mock_pool):
    from api.db.queries import get_dag_flow
    mock_pool.fetchrow.return_value = {
        "id": "general-v1",
        "stage_plan": [{"stage": "preprocess", "config": {"provider": "ffmpeg"}}],
        "is_default": True,
        "deprecated": False,
        "created_at": 1000.0,
    }
    result = await get_dag_flow(mock_pool, None)
    assert result["id"] == "general-v1"
    sql, *_ = mock_pool.fetchrow.call_args[0]
    assert "is_default = true" in sql

@pytest.mark.asyncio
async def test_get_dag_flow_by_id(mock_pool):
    from api.db.queries import get_dag_flow
    mock_pool.fetchrow.return_value = {
        "id": "course-v1",
        "stage_plan": [],
        "is_default": False,
        "deprecated": False,
        "created_at": 1000.0,
    }
    result = await get_dag_flow(mock_pool, "course-v1")
    assert result["id"] == "course-v1"
    sql, *args = mock_pool.fetchrow.call_args[0]
    assert "course-v1" in args
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate
pytest tests/test_db_queries.py -v
```

Expected: `ModuleNotFoundError: No module named 'api.db.queries'`

- [ ] **Step 3: Write api/db/queries.py**

```python
# api/db/queries.py
"""asyncpg-backed query functions. Each function takes pool: asyncpg.Pool as first arg."""
import json
import time
from typing import Any

import asyncpg


async def init(pool: asyncpg.Pool) -> None:
    """Run migrations if tables don't exist. Called from FastAPI lifespan."""
    import pathlib
    migrations_dir = pathlib.Path(__file__).parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        sql = sql_file.read_text()
        await pool.execute(sql)


async def upsert_job(pool: asyncpg.Pool, job_id: str, **kwargs: Any) -> None:
    cols = list(kwargs.keys())
    vals = list(kwargs.values())
    col_list = ", ".join(cols)
    placeholder_list = ", ".join(f"${i+2}" for i in range(len(cols)))
    update_list = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    sql = f"""
        INSERT INTO jobs (id, {col_list})
        VALUES ($1, {placeholder_list})
        ON CONFLICT (id) DO UPDATE SET {update_list}
    """
    await pool.execute(sql, job_id, *vals)


async def get_job(pool: asyncpg.Pool, job_id: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    return dict(row) if row else None


async def insert_event(
    pool: asyncpg.Pool, job_id: str, stage: str, status: str,
    retry_attempt: int = 0, error_msg: str | None = None,
    payload: str | None = None, ts: float | None = None,
) -> None:
    await pool.execute(
        """INSERT INTO events (job_id, stage, status, retry_attempt, error_msg, payload, ts)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        job_id, stage, status, retry_attempt, error_msg, payload, ts or time.time(),
    )


async def get_status_overview(pool: asyncpg.Pool) -> dict:
    active_statuses = ("queued", "processing")
    rows_processing = await pool.fetch(
        "SELECT * FROM jobs WHERE status = ANY($1::text[]) ORDER BY started_at DESC",
        list(active_statuses),
    )
    rows_queue = await pool.fetch(
        "SELECT * FROM jobs WHERE status = 'submitted' ORDER BY submitted_at ASC"
    )
    rows_recent = await pool.fetch(
        "SELECT * FROM jobs WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 20"
    )
    rows_failed = await pool.fetch(
        "SELECT * FROM jobs WHERE status = 'failed' ORDER BY completed_at DESC LIMIT 10"
    )
    return {
        "processing": [dict(r) for r in rows_processing],
        "queue":      [dict(r) for r in rows_queue],
        "recent":     [dict(r) for r in rows_recent],
        "failed":     [dict(r) for r in rows_failed],
    }


async def count_active_jobs(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('submitted','queued','processing')"
    )
    return row["count"] if row else 0


async def get_dag_flow(pool: asyncpg.Pool, flow_id: str | None) -> dict:
    if flow_id:
        row = await pool.fetchrow(
            "SELECT * FROM dag_flows WHERE id = $1 AND deprecated = false", flow_id
        )
    else:
        row = await pool.fetchrow(
            "SELECT * FROM dag_flows WHERE is_default = true AND deprecated = false LIMIT 1"
        )
    if not row:
        raise ValueError(f"dag_flow not found: {flow_id!r}")
    result = dict(row)
    if isinstance(result.get("stage_plan"), str):
        result["stage_plan"] = json.loads(result["stage_plan"])
    return result


async def get_task_aggregates(pool: asyncpg.Pool) -> dict:
    row = await pool.fetchrow(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(completed_at - started_at), 0) AS total_duration,
                  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
           FROM jobs"""
    )
    return {
        "total_tasks":        row["total"] or 0,
        "total_duration_sec": float(row["total_duration"] or 0),
        "completed":          row["completed"] or 0,
    }


async def get_stage_events(pool: asyncpg.Pool, job_id: str) -> list[dict]:
    rows = await pool.fetch(
        """SELECT stage, ts FROM events
           WHERE job_id = $1 AND status = 'success' AND stage IS NOT NULL
           ORDER BY ts ASC""",
        job_id,
    )
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Write api/db/__init__.py shim**

```python
# api/db/__init__.py
"""Public re-export shim.

Callers use `from api import db; await db.upsert_task(...)` — unchanged.
The shim pulls app.state.pool from the running FastAPI app so callers
don't need to pass the pool explicitly.
"""
from __future__ import annotations
import functools
from api.db.queries import (
    init, upsert_job, get_job, insert_event, get_status_overview,
    count_active_jobs, get_dag_flow, get_task_aggregates, get_stage_events,
)

__all__ = [
    "init", "upsert_job", "get_job", "insert_event", "get_status_overview",
    "count_active_jobs", "get_dag_flow", "get_task_aggregates", "get_stage_events",
    # legacy shims below
    "upsert_task", "get_task", "count_active_tasks",
]


def _get_pool():
    """Pull pool from running FastAPI app state."""
    from api.main import app
    return app.state.pool


# ── Legacy shims — same names as old api/db.py ──────────────────────────────

async def upsert_task(stem: str, **kwargs) -> None:
    """Legacy shim: stem maps to job_id; filename defaults to stem."""
    kw = dict(kwargs)
    if "filename" not in kw:
        kw["filename"] = stem
    await upsert_job(_get_pool(), stem, **kw)


async def get_task(stem: str) -> dict | None:
    return await get_job(_get_pool(), stem)


async def count_active_tasks() -> int:
    return await count_active_jobs(_get_pool())


async def get_status_overview_shim() -> dict:
    return await get_status_overview(_get_pool())


# Override module-level name so existing callers work:
import sys as _sys
_mod = _sys.modules[__name__]
_orig_get_status_overview = get_status_overview


async def get_status_overview():  # type: ignore[override]
    return await _orig_get_status_overview(_get_pool())
```

- [ ] **Step 5: Run tests — should pass now**

```bash
pytest tests/test_db_queries.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/db/ tests/test_db_queries.py
git commit -m "feat(db): asyncpg query module — jobs, dag_flows, events"
```

---

## Task 4: Wire asyncpg Pool into FastAPI Lifespan

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Read current lifespan in api/main.py**

Find the `@asynccontextmanager async def lifespan(app)` block. It currently calls `await db.init()` and starts the Redis consumer.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_db_pool.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_app_state_has_pool_after_lifespan():
    """Pool must be on app.state after lifespan startup."""
    import asyncpg
    fake_pool = MagicMock(spec=asyncpg.Pool)
    fake_pool.execute = AsyncMock()

    with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch("api.db.init", AsyncMock()):
        from api.main import app, lifespan
        from contextlib import asynccontextmanager
        async with lifespan(app):
            assert app.state.pool is fake_pool
```

- [ ] **Step 3: Run to confirm failure**

```bash
pytest tests/test_db_pool.py -v
```

Expected: FAIL — `AttributeError: 'Starlette' object has no attribute 'pool'`

- [ ] **Step 4: Add pool init to api/main.py lifespan**

Find the lifespan function and add at the top of the startup block:

```python
import asyncpg
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mediaflow:changeme@localhost:5432/mediaflow"
)

# inside lifespan startup:
app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
await db.init(app.state.pool)   # run migrations
```

And in the shutdown block:
```python
await app.state.pool.close()
```

- [ ] **Step 5: Run test — should pass**

```bash
pytest tests/test_db_pool.py -v
```

Expected: PASS.

- [ ] **Step 6: Smoke-test against running postgres**

```bash
DATABASE_URL=postgresql://mediaflow:changeme@localhost:5432/mediaflow \
  uvicorn api.main:app --port 8080 --reload &
curl http://localhost:8080/health
```

Expected: `{"status": "ok"}` with no traceback in logs.

- [ ] **Step 7: Commit**

```bash
git add api/main.py tests/test_db_pool.py
git commit -m "feat(api): wire asyncpg pool into FastAPI lifespan"
```

---

## Task 5: Migrate Existing Call Sites

**Files:**
- Modify: `api/event_processor.py`, `api/routes/status.py`, `api/routes/stats.py`, `api/routes/tasks.py`, `api/routes/upload.py`, `api/reconcile.py`, `api/mq/queue_consumer.py`, `api/cleanup.py`

- [ ] **Step 1: Run existing test suite to establish baseline**

```bash
pytest tests/ -v --ignore=tests/test_db_queries.py --ignore=tests/test_db_pool.py 2>&1 | tail -20
```

Record: N tests passing before migration.

- [ ] **Step 2: Replace api/db.py with re-export**

Replace the entire body of `api/db.py` with:

```python
# api/db.py — compatibility shim; delegates to api.db package
from api.db import *  # noqa: F401, F403
from api.db import (
    init, upsert_task, get_task, count_active_tasks,
    upsert_job, get_job, insert_event, get_status_overview,
    count_active_jobs, get_dag_flow, get_task_aggregates, get_stage_events,
)
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: same N tests passing (shim is transparent). Fix any import errors before continuing.

- [ ] **Step 4: Update call sites that use deprecated function names**

Search for callers of functions that were renamed:
- `count_active_tasks()` → now also available as `count_active_jobs()` (shim covers it)
- `upsert_task(stem, ...)` → shim maps to `upsert_job(pool, stem, ...)` transparently

No caller changes needed — shim handles it. Verify by running pytest again.

- [ ] **Step 5: Commit**

```bash
git add api/db.py
git commit -m "refactor(db): replace api/db.py with asyncpg shim — callers unchanged"
```

---

## Task 6: Integration Smoke Test

- [ ] **Step 1: Start all services**

```bash
docker compose up -d postgres redis minio
source venv/bin/activate
uvicorn api.main:app --port 8080 &
```

- [ ] **Step 2: Verify job upsert round-trip**

```bash
curl -s -X POST http://localhost:8080/events/stage-complete \
  -H "Content-Type: application/json" \
  -d '{"event":"task.submitted","stem":"test01","filename":"test01.m4a"}' | python3 -m json.tool
```

Expected: `{"ok": true}` (or equivalent success response).

- [ ] **Step 3: Verify dag_flows seed data via psql**

```bash
docker compose exec postgres psql -U mediaflow -d mediaflow \
  -c "SELECT id, is_default FROM dag_flows ORDER BY id;"
```

Expected:
```
   id        | is_default
-------------+-----------
 course-v1   | f
 general-v1  | t
 meeting-v1  | f
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(db): integration smoke test — PostgreSQL round-trip confirmed"
```

---

## Self-Review Checklist

- [ ] `dag_flows` table exists with 3 seed rows, `general-v1` is default
- [ ] `jobs` table has all columns from architecture diagram (incl. `queued` in CHECK constraint)
- [ ] `events` table has `payload TEXT` column
- [ ] `api/db.py` callers (`event_processor.py`, `routes/`, `reconcile.py`) work without changes
- [ ] `api.db.get_dag_flow(pool, None)` returns the `is_default = true` row
- [ ] `asyncpg.create_pool()` is called exactly once, in the lifespan startup
- [ ] Pool is closed in lifespan shutdown
- [ ] All tests in `tests/test_db_queries.py` pass with mocked pool (no real DB)
- [ ] No `aiosqlite` calls remain in new `api/db/queries.py`
