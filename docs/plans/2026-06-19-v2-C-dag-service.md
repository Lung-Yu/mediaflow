# Sub-plan C: DAG-Service

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add orchestration layer — `POST /internal/trigger` receives a job-id + flow-id from Project Service, builds the stage_plan from dag_flows, enqueues into Redis `mediaflow:jobs` stream, handles stage-completion callbacks from Progress Worker, and owns retry logic.

**Architecture:** `api/services/dag.py` owns all orchestration logic. New route `api/routes/dag_callback.py` exposes `POST /internal/stage-callback`. The existing Redis Streams publisher is replaced by a new XADD call targeting `mediaflow:jobs` with the new MQ message schema.

**Tech Stack:** asyncpg (pool from app.state), redis-py (already in requirements), FastAPI routes

**Depends on:** Sub-plan A (asyncpg pool + dag_flows table)

## Global Constraints

- New MQ stream key: `mediaflow:jobs` (old `mediaflow:events` kept for legacy compatibility; will be removed in Sub-plan D)
- All MQ values are strings (Redis requirement) — serialize lists/dicts as JSON strings
- Retry decision lives in DAG-Service, not in Progress Worker
- `retry_backoff_sec` is a sleep before re-enqueue (run in background task, not blocking the callback)
- Internal routes (`/internal/*`) are not authenticated in Phase 1 — they are Docker-network-only

---

## File Structure

```
# Create
api/services/__init__.py
api/services/dag.py            — orchestration: trigger, enqueue, retry, final-fail
api/routes/dag_callback.py     — POST /internal/stage-callback

# Modify
api/main.py                    — include dag_callback router
api/main.py                    — init Redis client in lifespan (store in app.state.redis)

# Test
tests/test_dag_service.py      — unit tests (mock pool + mock redis)
tests/test_dag_callback.py     — route tests (TestClient + mocked dag service)
```

---

## Interfaces

**Consumes (from Sub-plan A):**
```python
await db.get_dag_flow(pool, flow_id: str | None) -> dict
await db.upsert_job(pool, job_id: str, **kwargs) -> None
await db.insert_event(pool, job_id: str, stage: str, status: str, **kwargs) -> None
await db.get_job(pool, job_id: str) -> dict | None
```

**Produces (consumed by Sub-plan D Progress Worker and Sub-plan E Project Service):**
```python
# api/services/dag.py
async def trigger_job(pool, redis, job_id: str, flow_id: str | None,
                      minio_processing_key: str) -> None: ...
async def handle_stage_callback(pool, redis, job_id: str, stage: str,
                                 status: str, retry_attempt: int,
                                 error_msg: str | None) -> None: ...

# MQ message enqueued to mediaflow:jobs (XADD):
{
    "job_id":            "abc123",
    "processing_path":   "processing/abc123/lesson01.wav",
    "stage_plan":        '[{"stage":"preprocess","config":{...}}, ...]',  # JSON string
    "retry_attempt":     "0",
    "resume_from_stage": "preprocess"
}

# HTTP callback body (POST /internal/stage-callback):
{
    "job_id":        "abc123",
    "stage":         "transcribe",
    "status":        "success",   # | "failed"
    "retry_attempt": 0,
    "error_msg":     null
}
```

---

## Task 1: Redis Client in FastAPI Lifespan

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_dag_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_app_state_has_redis_after_lifespan():
    import redis.asyncio as aioredis
    fake_redis = MagicMock(spec=aioredis.Redis)
    fake_redis.ping = AsyncMock(return_value=True)
    fake_pool = MagicMock()
    fake_pool.execute = AsyncMock()
    with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch("redis.asyncio.from_url", return_value=fake_redis), \
         patch("api.db.init", AsyncMock()):
        from api.main import app, lifespan
        async with lifespan(app):
            assert app.state.redis is fake_redis
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_dag_service.py::test_app_state_has_redis_after_lifespan -v
```

Expected: FAIL — `AttributeError: 'Starlette' object has no attribute 'redis'`

- [ ] **Step 3: Add redis.asyncio client to api/main.py lifespan**

```python
import redis.asyncio as aioredis
import os

REDIS_URL = f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}"

# inside lifespan startup (after pool init):
app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)

# inside lifespan shutdown:
await app.state.redis.aclose()
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_dag_service.py::test_app_state_has_redis_after_lifespan -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_dag_service.py
git commit -m "feat(api): add redis.asyncio client to FastAPI lifespan"
```

---

## Task 2: DAG Service — trigger_job

**Files:**
- Create: `api/services/__init__.py`
- Create: `api/services/dag.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dag_service.py — add

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
    return pool

@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.xadd = AsyncMock(return_value="1234-0")
    return r

@pytest.mark.asyncio
async def test_trigger_job_enqueues_to_mq(mock_pool, mock_redis):
    from api.services.dag import trigger_job
    import json
    mock_pool.fetchrow.return_value = {
        "id": "general-v1",
        "stage_plan": [{"stage": "preprocess", "config": {"provider": "ffmpeg"}},
                       {"stage": "transcribe", "config": {"provider": "mlx-whisper"}}],
        "is_default": True, "deprecated": False, "created_at": 1000.0,
    }
    mock_pool.execute.return_value = None

    await trigger_job(mock_pool, mock_redis,
                      job_id="job1", flow_id=None,
                      minio_processing_key="processing/job1/test.wav")

    assert mock_redis.xadd.called
    stream, fields = mock_redis.xadd.call_args[0]
    assert stream == "mediaflow:jobs"
    assert fields["job_id"] == "job1"
    assert fields["resume_from_stage"] == "preprocess"
    stage_plan = json.loads(fields["stage_plan"])
    assert stage_plan[0]["stage"] == "preprocess"

@pytest.mark.asyncio
async def test_trigger_job_sets_status_queued(mock_pool, mock_redis):
    from api.services.dag import trigger_job
    mock_pool.fetchrow.return_value = {
        "id": "general-v1",
        "stage_plan": [{"stage": "preprocess", "config": {}}],
        "is_default": True, "deprecated": False, "created_at": 1000.0,
    }
    mock_pool.execute.return_value = None

    await trigger_job(mock_pool, mock_redis,
                      job_id="job1", flow_id=None,
                      minio_processing_key="processing/job1/test.wav")

    # upsert_job must have been called with status='queued'
    call_args_list = mock_pool.execute.call_args_list
    sql_calls = [str(c[0][0]) for c in call_args_list]
    assert any("queued" in sql or "INSERT INTO jobs" in sql for sql in sql_calls)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_dag_service.py::test_trigger_job_enqueues_to_mq -v
```

Expected: FAIL — `ImportError: cannot import name 'trigger_job'`

- [ ] **Step 3: Write api/services/dag.py**

```python
# api/services/dag.py
"""DAG orchestration — trigger jobs, handle stage callbacks, own retry logic."""
import asyncio
import json
import logging
import os
import time

import asyncpg
import redis.asyncio as aioredis

from api import db

log = logging.getLogger(__name__)

JOBS_STREAM   = os.getenv("MQ_JOBS_STREAM", "mediaflow:jobs")
MAX_RETRIES   = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF = int(os.getenv("RETRY_BACKOFF_SEC", "30"))


async def trigger_job(
    pool: asyncpg.Pool,
    redis: aioredis.Redis,
    job_id: str,
    flow_id: str | None,
    minio_processing_key: str,
    retry_attempt: int = 0,
    resume_from_stage: str | None = None,
) -> None:
    """Look up dag_flow, set status=queued, enqueue to MQ."""
    flow = await db.get_dag_flow(pool, flow_id)
    stage_plan = flow["stage_plan"]
    if isinstance(stage_plan, list):
        stage_plan_json = json.dumps(stage_plan)
    else:
        stage_plan_json = stage_plan

    first_stage = stage_plan[0]["stage"] if isinstance(stage_plan, list) \
                  else json.loads(stage_plan_json)[0]["stage"]
    resume = resume_from_stage or first_stage

    await db.upsert_job(pool, job_id,
                        status="queued",
                        dag_flow_id=flow["id"],
                        current_stage=resume,
                        minio_processing_key=minio_processing_key)

    await redis.xadd(JOBS_STREAM, {
        "job_id":            job_id,
        "processing_path":   minio_processing_key,
        "stage_plan":        stage_plan_json,
        "retry_attempt":     str(retry_attempt),
        "resume_from_stage": resume,
    })
    log.info("Enqueued job %s (flow=%s, resume=%s, attempt=%d)",
             job_id, flow["id"], resume, retry_attempt)


async def handle_stage_callback(
    pool: asyncpg.Pool,
    redis: aioredis.Redis,
    job_id: str,
    stage: str,
    status: str,
    retry_attempt: int = 0,
    error_msg: str | None = None,
) -> None:
    """Called when Progress Worker reports a stage result."""
    await db.insert_event(pool, job_id, stage=stage, status=status,
                          retry_attempt=retry_attempt, error_msg=error_msg,
                          ts=time.time())

    if status == "success":
        await db.upsert_job(pool, job_id, current_stage=stage, status="processing")
        log.info("Stage %s succeeded for job %s", stage, job_id)
        return

    # Stage failed — retry decision
    job = await db.get_job(pool, job_id)
    retry_count = (job or {}).get("retry_count", 0)

    if retry_count < MAX_RETRIES:
        new_count = retry_count + 1
        await db.upsert_job(pool, job_id, retry_count=new_count, status="queued")
        log.warning("Job %s stage %s failed (attempt %d/%d) — retrying in %ds",
                    job_id, stage, new_count, MAX_RETRIES, RETRY_BACKOFF)
        processing_key = (job or {}).get("minio_processing_key", "")
        dag_flow_id    = (job or {}).get("dag_flow_id")
        asyncio.create_task(_retry_after(
            pool, redis, job_id, dag_flow_id, processing_key,
            new_count, stage,
        ))
    else:
        await db.upsert_job(pool, job_id, status="failed", error_msg=error_msg,
                            completed_at=time.time())
        log.error("Job %s permanently failed after %d attempts: %s",
                  job_id, retry_count, error_msg)


async def _retry_after(
    pool, redis, job_id, flow_id, processing_key, retry_attempt, resume_stage
) -> None:
    await asyncio.sleep(RETRY_BACKOFF)
    await trigger_job(pool, redis, job_id, flow_id, processing_key,
                      retry_attempt=retry_attempt, resume_from_stage=resume_stage)
```

- [ ] **Step 4: Create api/services/__init__.py**

```python
# api/services/__init__.py
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_dag_service.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/ tests/test_dag_service.py
git commit -m "feat(dag): trigger_job + handle_stage_callback + retry logic"
```

---

## Task 3: Stage-Callback Route

**Files:**
- Create: `api/routes/dag_callback.py`
- Modify: `api/main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_dag_callback.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)

def test_stage_callback_success(client):
    with patch("api.services.dag.handle_stage_callback", AsyncMock()) as mock_cb:
        resp = client.post("/internal/stage-callback", json={
            "job_id": "job1",
            "stage": "transcribe",
            "status": "success",
            "retry_attempt": 0,
            "error_msg": None,
        })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert mock_cb.called

def test_stage_callback_failure_triggers_retry(client):
    with patch("api.services.dag.handle_stage_callback", AsyncMock()) as mock_cb:
        resp = client.post("/internal/stage-callback", json={
            "job_id": "job1",
            "stage": "transcribe",
            "status": "failed",
            "retry_attempt": 0,
            "error_msg": "timeout",
        })
    assert resp.status_code == 200
    assert mock_cb.called

def test_stage_callback_invalid_status_rejected(client):
    resp = client.post("/internal/stage-callback", json={
        "job_id": "job1",
        "stage": "transcribe",
        "status": "unknown_status",
        "retry_attempt": 0,
    })
    assert resp.status_code == 422  # Pydantic validation error
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_dag_callback.py -v
```

Expected: FAIL — 404 Not Found (route doesn't exist yet)

- [ ] **Step 3: Write api/routes/dag_callback.py**

```python
# api/routes/dag_callback.py
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.services import dag

router = APIRouter(prefix="/internal", tags=["internal"])


class StageCallbackBody(BaseModel):
    job_id: str
    stage: str
    status: Literal["success", "failed"]
    retry_attempt: int = 0
    error_msg: str | None = None


@router.post("/stage-callback")
async def stage_callback(body: StageCallbackBody, request: Request):
    pool  = request.app.state.pool
    redis = request.app.state.redis
    await dag.handle_stage_callback(
        pool, redis,
        job_id=body.job_id,
        stage=body.stage,
        status=body.status,
        retry_attempt=body.retry_attempt,
        error_msg=body.error_msg,
    )
    return {"ok": True}
```

- [ ] **Step 4: Register router in api/main.py**

```python
from api.routes import dag_callback
app.include_router(dag_callback.router)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_dag_callback.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/dag_callback.py api/main.py tests/test_dag_callback.py
git commit -m "feat(api): POST /internal/stage-callback — DAG stage result handler"
```

---

## Task 4: GET /jobs/{id} — Job Status Endpoint

**Files:**
- Create: `api/routes/jobs.py`
- Modify: `api/main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_jobs_route.py
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)

def test_get_job_returns_job_dict(client):
    fake_job = {
        "id": "job1", "filename": "test.m4a", "status": "completed",
        "submitted_by": "anonymous", "dag_flow_id": "general-v1",
        "current_stage": "summarize", "submitted_at": 1000.0,
        "started_at": 1001.0, "completed_at": 1010.0, "retry_count": 0,
        "error_msg": None, "output_srt_path": None,
        "corrected_srt_path": None, "verification_status": "unverified",
        "verified_at": None, "verified_by": None,
        "minio_input_key": "input/test.m4a",
        "minio_processing_key": "processing/job1/test.wav",
    }
    with patch("api.db.get_job", AsyncMock(return_value=fake_job)):
        resp = client.get("/jobs/job1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "job1"

def test_get_job_not_found(client):
    with patch("api.db.get_job", AsyncMock(return_value=None)):
        resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_jobs_route.py -v
```

Expected: FAIL — 404 (route not registered)

- [ ] **Step 3: Write api/routes/jobs.py**

```python
# api/routes/jobs.py
from fastapi import APIRouter, HTTPException, Request
from api import db

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    pool = request.app.state.pool
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job


@router.get("")
async def list_jobs(request: Request):
    pool = request.app.state.pool
    overview = await db.get_status_overview(pool)
    return overview
```

- [ ] **Step 4: Register router in api/main.py**

```python
from api.routes import jobs as jobs_router
app.include_router(jobs_router.router)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_jobs_route.py tests/test_dag_callback.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/jobs.py api/main.py tests/test_jobs_route.py
git commit -m "feat(api): GET /jobs/{id} + GET /jobs — job status endpoints"
```

---

## Self-Review Checklist

- [ ] `trigger_job(pool, redis, job_id, None, processing_key)` selects the `is_default = true` flow
- [ ] `handle_stage_callback` with `status="failed"` and `retry_count < MAX_RETRIES` calls `_retry_after` as a background task (not blocking)
- [ ] `handle_stage_callback` with `status="failed"` and `retry_count >= MAX_RETRIES` sets `jobs.status = 'failed'`
- [ ] `POST /internal/stage-callback` with `status="unknown"` returns HTTP 422
- [ ] `GET /jobs/{id}` returns 404 for unknown job_id
- [ ] MQ message fields `stage_plan` is a JSON string (not a list) — Redis only stores strings
- [ ] `retry_attempt` and `resume_from_stage` are present in every XADD message
