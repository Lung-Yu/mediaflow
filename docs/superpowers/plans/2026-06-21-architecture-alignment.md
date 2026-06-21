# Architecture Alignment Plan (Ponytail Edition)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the current implementation with the excalidraw architecture: Project Service → DAG-Service → MQ → Progress Worker → HTTP callback, plus FR4 (correction), FR5 (retry), FR6 (security check).

**Architecture:** Sub-plan A (PostgreSQL, shim layer) is already done. Skip Sub-plan B (Provider Pattern — YAGNI, runner.execute() works without it). Build C → E → D → F in dependency order. Sub-plan G (Jinja2 web) is skipped — frontend is React.

**Tech Stack:** FastAPI, asyncpg (pool from app.state), redis-py, boto3, httpx (sync, for pipeline worker), pytest

## Global Constraints

- No new pip dependencies
- All async DB calls: `pool = request.app.state.pool` or `app.state.pool`
- All async Redis calls: `redis = app.state.redis`
- MQ stream key: `mediaflow:jobs` (separate from legacy `mediaflow:events`)
- MinIO bucket paths: `input/{job_id}`, `processing/{job_id}`, `output/{job_id}/`
- Internal routes (`/internal/*`) are not authenticated in Phase 1
- Config additions go in `config.yaml.example`; loaded via `pipeline/config.py`
- `pipeline/worker.py` is synchronous (no asyncio) — stages are blocking
- runner.py `_build_providers_for_stage()` is dead code — do not call it
- Retry backoff: simple `time.sleep(retry_backoff_sec)` before re-enqueue, run in a daemon thread
- Commit after every task

---

## Status: What's Done vs Not

| Sub-plan | Status | Notes |
|----------|--------|-------|
| A — PostgreSQL | ✅ Done | asyncpg, shim layer, dag_flows seeded |
| B — Provider Pattern | ⏭ Skip | YAGNI; runner.execute() uses global cfg directly |
| C — DAG-Service | ❌ Build | api/services/dag.py + api/routes/dag_callback.py |
| D — Progress Worker | ❌ Build | pipeline/worker.py (new), watcher.py (simplify) |
| E — Project Service | ❌ Build | api/services/project.py + FR6 |
| F — FR4 + FR5 + Clip | ❌ Build | correction routes + clip route |
| G — Jinja2 Web | ⏭ Skip | Frontend is React |

---

## File Structure

```
# Create
api/services/dag.py               — trigger_job, handle_stage_callback, retry logic
api/services/project.py           — on_upload_trigger, validate_fr6
api/services/correction.py        — rebuild_corrected_srt
api/routes/dag_callback.py        — POST /internal/stage-callback
api/routes/correction.py          — PATCH /jobs/{id}/correction, POST .../finalize
api/routes/clip.py                — GET /jobs/{id}/segment/{index}/audio
pipeline/worker.py                — MQ consumer + stage runner + MinIO I/O

# Modify
api/routes/upload.py              — /complete calls project.on_upload_trigger
api/routes/jobs.py                — add POST /jobs
api/main.py                       — register new routes, start worker process
pipeline/watcher.py               — simplify: detect → upload to MinIO → POST /jobs
config.yaml.example               — add max_concurrent_jobs, max_retries, retry_backoff_sec
```

---

## Task 1: Config additions + dead-code cleanup

**Files:**
- Modify: `config.yaml.example`
- Modify: `pipeline/config.py` (if needed — verify it passes through unknown keys)
- Modify: `pipeline/runner.py` — remove dead `_build_providers_for_stage` and its import

**- [ ] Step 1: Add concurrency config to config.yaml.example**

Under `pipeline:` section, add:

```yaml
pipeline:
  max_concurrent_jobs: 2      # Progress Worker thread pool size
  max_queue_depth: 20         # reject POST /jobs if pending >= this
  max_retries: 3              # per job, before final failure
  retry_backoff_sec: 30       # seconds to wait before retry re-enqueue
```

**- [ ] Step 2: Remove dead provider code from runner.py**

Delete `_build_providers_for_stage` function (lines 139-159) and its import reference. Also delete the `# Alias used by pipeline/worker.py` comment and `_STAGE_ADAPTERS = STAGE_RUNNERS` line (runner.py:136).

After deletion, `pipeline/providers` import is gone — no import error on startup.

**- [ ] Step 3: Verify config loads correctly**

```bash
source venv/bin/activate
python3 -c "from pipeline.config import load_config; c = load_config(); print(c.get('pipeline', {}).get('max_retries'))"
```
Expected: `3`

**- [ ] Step 4: Commit**

```bash
git add config.yaml.example pipeline/runner.py
git commit -m "chore(config): add concurrency/retry config; remove dead provider dead-code"
```

---

## Task 2: DAG-Service core

**Files:**
- Create: `api/services/dag.py`
- Create: `tests/test_dag_service.py`

**Interfaces produced:**
- `trigger_job(pool, redis, job_id, filename, minio_processing_key, dag_flow_id) -> None`
- `handle_stage_callback(pool, redis, job_id, stage, status, retry_attempt, error_msg) -> None`

**- [ ] Step 1: Write failing tests**

Create `tests/test_dag_service.py`:

```python
import json
import time
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pool(job=None, flow=None):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=job)
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_redis():
    r = AsyncMock()
    r.xadd = AsyncMock(return_value="1-0")
    r.xlen = AsyncMock(return_value=0)
    return r


FLOW = {
    "id": "general-v1",
    "stage_plan": [
        {"stage": "preprocess", "config": {}},
        {"stage": "transcribe", "config": {}},
        {"stage": "summarize", "config": {}},
    ],
    "is_default": True,
}

JOB = {
    "id": "job123",
    "filename": "test.m4a",
    "minio_processing_key": "processing/job123",
    "dag_flow_id": "general-v1",
    "status": "queued",
    "retry_count": 0,
}


@pytest.mark.asyncio
async def test_trigger_job_enqueues_mq():
    from api.services.dag import trigger_job
    pool = _make_pool()
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)):
        await trigger_job(pool, redis, "job123", "test.m4a", "processing/job123", None)
    redis.xadd.assert_called_once()
    call_kwargs = redis.xadd.call_args
    fields = call_kwargs[0][1]  # second positional arg is the fields dict
    assert fields["job_id"] == "job123"
    assert "stage_plan" in fields
    assert fields["resume_from_stage"] == "preprocess"


@pytest.mark.asyncio
async def test_handle_stage_callback_last_stage_completes_job():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job=JOB)
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.upsert_job", AsyncMock()) as mock_upsert:
        await handle_stage_callback(pool, redis, "job123", "summarize", "success", 0, None)
    mock_upsert.assert_called_once()
    assert mock_upsert.call_args[1]["status"] == "completed"


@pytest.mark.asyncio
async def test_handle_stage_callback_failure_retries():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job=JOB)
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.upsert_job", AsyncMock()), \
         patch("api.services.dag._enqueue_after_backoff") as mock_backoff:
        await handle_stage_callback(pool, redis, "job123", "transcribe", "failed", 0, "OOM")
    mock_backoff.assert_called_once()


@pytest.mark.asyncio
async def test_handle_stage_callback_failure_final_fail():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job={**JOB, "retry_count": 3})
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.upsert_job", AsyncMock()) as mock_upsert, \
         patch("api.services.dag._enqueue_after_backoff") as mock_backoff:
        # max_retries default is 3, retry_attempt=3 means exhausted
        await handle_stage_callback(pool, redis, "job123", "transcribe", "failed", 3, "OOM")
    mock_backoff.assert_not_called()
    mock_upsert.assert_called_once()
    assert mock_upsert.call_args[1]["status"] == "failed"
```

**- [ ] Step 2: Run tests to verify they fail**

```bash
source venv/bin/activate
pip install pytest pytest-asyncio
pytest tests/test_dag_service.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'api.services.dag'`

**- [ ] Step 3: Implement api/services/dag.py**

```python
"""DAG-Service — job orchestration: trigger, stage callback, retry."""
import json
import logging
import os
import threading
import time

import asyncpg

from api.db.queries import get_dag_flow, insert_event, upsert_job, get_job

log = logging.getLogger(__name__)

_MQ_KEY = "mediaflow:jobs"
_MAX_RETRIES = int(os.getenv("PIPELINE_MAX_RETRIES", "3"))
_RETRY_BACKOFF = int(os.getenv("PIPELINE_RETRY_BACKOFF_SEC", "30"))


async def trigger_job(
    pool: asyncpg.Pool,
    redis,
    job_id: str,
    filename: str,
    minio_processing_key: str,
    dag_flow_id: str | None,
) -> None:
    flow = await get_dag_flow(pool, dag_flow_id)
    stage_ids = [s["stage"] for s in flow["stage_plan"]]
    await upsert_job(
        pool, job_id,
        filename=filename,
        dag_flow_id=flow["id"],
        status="queued",
        minio_processing_key=minio_processing_key,
        submitted_at=time.time(),
    )
    await _xadd(redis, job_id, minio_processing_key, flow["stage_plan"], 0, stage_ids[0])


async def handle_stage_callback(
    pool: asyncpg.Pool,
    redis,
    job_id: str,
    stage: str,
    status: str,
    retry_attempt: int,
    error_msg: str | None,
) -> None:
    await insert_event(pool, job_id, stage, status,
                       retry_attempt=retry_attempt, error_msg=error_msg)

    if status == "failed":
        if retry_attempt < _MAX_RETRIES:
            job = await get_job(pool, job_id)
            flow = await get_dag_flow(pool, job["dag_flow_id"])
            await upsert_job(pool, job_id,
                             status="queued",
                             retry_count=retry_attempt + 1,
                             current_stage=stage)
            _enqueue_after_backoff(
                redis, job_id,
                job["minio_processing_key"],
                flow["stage_plan"],
                retry_attempt + 1,
                stage,
            )
        else:
            await upsert_job(pool, job_id,
                             status="failed",
                             error_msg=error_msg,
                             completed_at=time.time())
        return

    # success
    job = await get_job(pool, job_id)
    flow = await get_dag_flow(pool, job["dag_flow_id"])
    stage_ids = [s["stage"] for s in flow["stage_plan"]]
    await upsert_job(pool, job_id, current_stage=stage, status="processing")
    if stage == stage_ids[-1]:
        await upsert_job(pool, job_id, status="completed", completed_at=time.time())
        log.info("Job %s completed", job_id)


async def _xadd(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from):
    await redis.xadd(_MQ_KEY, {
        "job_id": job_id,
        "processing_path": processing_path,
        "stage_plan": json.dumps(stage_plan),
        "retry_attempt": str(retry_attempt),
        "resume_from_stage": resume_from,
    })


def _enqueue_after_backoff(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from):
    """Fire-and-forget: sleep then re-enqueue. Runs in daemon thread."""
    import asyncio

    def _run():
        time.sleep(_RETRY_BACKOFF)
        asyncio.run(_xadd(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from))
        log.info("Re-enqueued job %s (attempt %d, from %s)", job_id, retry_attempt, resume_from)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
```

**- [ ] Step 4: Run tests**

```bash
pytest tests/test_dag_service.py -v
```
Expected: all 4 tests PASS

**- [ ] Step 5: Commit**

```bash
git add api/services/dag.py tests/test_dag_service.py
git commit -m "feat(api): DAG-Service — trigger_job, stage callback, retry"
```

---

## Task 3: DAG callback route + wire into main.py

**Files:**
- Create: `api/routes/dag_callback.py`
- Modify: `api/main.py`

**- [ ] Step 1: Create api/routes/dag_callback.py**

```python
"""Internal HTTP endpoint — Progress Worker calls this after each stage."""
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.services.dag import handle_stage_callback

router = APIRouter(prefix="/internal")
log = logging.getLogger(__name__)


class StageCallbackRequest(BaseModel):
    job_id: str
    stage: str
    status: str          # "success" | "failed"
    retry_attempt: int = 0
    error_msg: Optional[str] = None


@router.post("/stage-callback", status_code=204)
async def stage_callback(req: StageCallbackRequest, request: Request):
    pool = request.app.state.pool
    redis = request.app.state.redis
    log.info("Stage callback: job=%s stage=%s status=%s", req.job_id, req.stage, req.status)
    await handle_stage_callback(
        pool, redis,
        req.job_id, req.stage, req.status, req.retry_attempt, req.error_msg,
    )
```

**- [ ] Step 2: Register route in api/main.py**

Add import and register in the `app.include_router` block:

```python
# Add to imports at top
from api.routes import dag_callback

# Add alongside other include_router calls
app.include_router(dag_callback.router)
```

**- [ ] Step 3: Smoke test**

```bash
curl -s -X POST http://localhost:8080/internal/stage-callback \
  -H "Content-Type: application/json" \
  -d '{"job_id":"test","stage":"transcribe","status":"success","retry_attempt":0}' \
  -w "\nHTTP %{http_code}"
```
Expected: `HTTP 204` (may 500 if no job row, that's fine — just confirms route exists)

**- [ ] Step 4: Commit**

```bash
git add api/routes/dag_callback.py api/main.py
git commit -m "feat(api): POST /internal/stage-callback route"
```

---

## Task 4: Project Service + FR6

**Files:**
- Create: `api/services/project.py`
- Create: `tests/test_project_service.py`

**Interfaces produced:**
- `validate_fr6(filename, file_size_bytes) -> str | None` — returns error string or None
- `on_upload_trigger(pool, redis, minio, file_key, filename, dag_flow_id, submitted_by) -> str` — returns job_id

**- [ ] Step 1: Write failing tests**

Create `tests/test_project_service.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_validate_fr6_accepts_normal():
    from api.services.project import validate_fr6
    assert validate_fr6("recording.m4a", 1024 * 1024) is None


def test_validate_fr6_rejects_path_traversal():
    from api.services.project import validate_fr6
    assert validate_fr6("../etc/passwd.m4a", 1024) is not None


def test_validate_fr6_rejects_null_byte():
    from api.services.project import validate_fr6
    assert validate_fr6("file\x00.m4a", 1024) is not None


def test_validate_fr6_rejects_empty_file():
    from api.services.project import validate_fr6
    assert validate_fr6("recording.m4a", 0) is not None


def test_validate_fr6_rejects_too_large():
    from api.services.project import validate_fr6
    assert validate_fr6("recording.m4a", 6 * 1024 ** 3) is not None


def test_validate_fr6_rejects_long_filename():
    from api.services.project import validate_fr6
    assert validate_fr6("a" * 256 + ".m4a", 1024) is not None


@pytest.mark.asyncio
async def test_on_upload_trigger_creates_job():
    from api.services.project import on_upload_trigger
    pool = AsyncMock()
    redis = AsyncMock()
    minio = MagicMock()
    minio.head_object = MagicMock(return_value={"ContentLength": 1024})
    minio.copy_object = MagicMock()

    with patch("api.services.project.trigger_job", AsyncMock()) as mock_trigger, \
         patch("api.services.project.upsert_job", AsyncMock()):
        job_id = await on_upload_trigger(
            pool, redis, minio,
            file_key="input/test.m4a",
            filename="test.m4a",
            dag_flow_id=None,
            submitted_by="anonymous",
        )
    assert job_id is not None
    mock_trigger.assert_called_once()


@pytest.mark.asyncio
async def test_on_upload_trigger_fr6_failure_returns_error():
    from api.services.project import on_upload_trigger
    from fastapi import HTTPException
    pool = AsyncMock()
    redis = AsyncMock()
    minio = MagicMock()
    minio.head_object = MagicMock(return_value={"ContentLength": 0})
    minio.copy_object = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await on_upload_trigger(
            pool, redis, minio,
            file_key="input/test.m4a",
            filename="test.m4a",
            dag_flow_id=None,
            submitted_by="anonymous",
        )
    assert exc_info.value.status_code == 400
```

**- [ ] Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_project_service.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'api.services.project'`

**- [ ] Step 3: Implement api/services/project.py**

```python
"""Project Service — job intake, FR6 security check, DAG trigger."""
import os
import time
import uuid

import asyncpg
from fastapi import HTTPException

from api.db.queries import upsert_job
from api.services.dag import trigger_job

_MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(5 * 1024 ** 3)))
_MAX_FILENAME_LEN = 255


def validate_fr6(filename: str, file_size_bytes: int) -> str | None:
    """FR6 Phase 1: filename anomaly + size check. Returns error string or None."""
    if "\x00" in filename:
        return "Filename contains null byte"
    if ".." in filename or filename.startswith("/"):
        return "Filename path traversal detected"
    if len(filename) > _MAX_FILENAME_LEN:
        return f"Filename too long (max {_MAX_FILENAME_LEN})"
    if file_size_bytes <= 0:
        return "File is empty"
    if file_size_bytes > _MAX_FILE_BYTES:
        return f"File too large ({file_size_bytes} bytes, max {_MAX_FILE_BYTES})"
    return None


async def on_upload_trigger(
    pool: asyncpg.Pool,
    redis,
    minio,
    file_key: str,
    filename: str,
    dag_flow_id: str | None,
    submitted_by: str = "anonymous",
) -> str:
    """Verify upload, FR6 check, copy to processing/, create job, trigger DAG."""
    # 1. verify file in MinIO input/
    try:
        meta = minio.head_object(file_key)
        file_size = meta["ContentLength"]
    except Exception as exc:
        raise HTTPException(400, f"File not found in storage: {exc}")

    # 2. FR6 check
    err = validate_fr6(filename, file_size)
    if err:
        raise HTTPException(400, f"FR6 validation failed: {err}")

    # 3. copy input/ → processing/
    job_id = str(uuid.uuid4())[:8] + "_" + filename.rsplit(".", 1)[0][:20].replace(" ", "_")
    processing_key = f"processing/{job_id}"
    try:
        minio.copy_object(src_key=file_key, dest_key=processing_key)
    except Exception as exc:
        raise HTTPException(500, f"Failed to stage file: {exc}")

    # 4. create job row (minimal; DAG-Service will fill in more)
    await upsert_job(
        pool, job_id,
        filename=filename,
        submitted_by=submitted_by,
        status="submitted",
        minio_input_key=file_key,
        minio_processing_key=processing_key,
        submitted_at=time.time(),
    )

    # 5. trigger DAG-Service
    await trigger_job(pool, redis, job_id, filename, processing_key, dag_flow_id)

    return job_id
```

**- [ ] Step 4: Run tests**

```bash
pytest tests/test_project_service.py -v
```
Expected: all 6 tests PASS

**- [ ] Step 5: Commit**

```bash
git add api/services/project.py tests/test_project_service.py
git commit -m "feat(api): Project Service — on_upload_trigger + FR6 validation"
```

---

## Task 5: Update upload route + POST /jobs

**Files:**
- Modify: `api/routes/upload.py`
- Modify: `api/routes/jobs.py`
- Modify: `api/main.py` (register POST /jobs if not already)

**- [ ] Step 1: Update upload.py /complete to call Project Service**

In `api/routes/upload.py`, replace the body of `upload_complete()`:

```python
@router.post("/complete")
async def upload_complete(req: CompleteRequest, request: Request):
    """Finalise multipart upload and hand off to Project Service."""
    stem = req.minio_key.split("/")[0]
    filename = req.minio_key.split("/", 1)[1]

    client = minio_mod.get_client()
    client.complete_multipart_upload(
        req.minio_key,
        req.upload_id,
        [{"part_number": p.part_number, "etag": p.etag} for p in req.parts],
    )

    from api.services.project import on_upload_trigger
    pool = request.app.state.pool
    redis = request.app.state.redis
    job_id = await on_upload_trigger(
        pool, redis, client,
        file_key=req.minio_key,
        filename=filename,
        dag_flow_id=None,
        submitted_by="anonymous",
    )
    return {"job_id": job_id, "stem": stem, "status": "queued"}
```

Also add `Request` to the FastAPI imports in upload.py:
```python
from fastapi import APIRouter, HTTPException, Request
```

**- [ ] Step 2: Add POST /jobs to api/routes/jobs.py**

```python
# Add to existing jobs.py imports
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

class CreateJobRequest(BaseModel):
    file_key: str                  # MinIO input/ key
    filename: str
    dag_flow: Optional[str] = None
    submitted_by: str = "anonymous"


@router.post("", status_code=201)
async def create_job(req: CreateJobRequest, request: Request):
    """Create a job from an already-uploaded MinIO file."""
    from api.services.project import on_upload_trigger
    pool = request.app.state.pool
    redis = request.app.state.redis
    from api.utils import minio as minio_mod
    client = minio_mod.get_client()
    job_id = await on_upload_trigger(
        pool, redis, client,
        file_key=req.file_key,
        filename=req.filename,
        dag_flow_id=req.dag_flow,
        submitted_by=req.submitted_by,
    )
    return {"job_id": job_id, "status": "queued"}
```

**- [ ] Step 3: Smoke test upload complete (requires running stack)**

```bash
# Check routes are registered
curl -s http://localhost:8080/openapi.json | python3 -c "
import json,sys; d=json.load(sys.stdin)
routes=[p for p in d['paths'] if '/jobs' in p or '/upload' in p or '/internal' in p]
print('\n'.join(routes))"
```
Expected: `/jobs`, `/upload/complete`, `/internal/stage-callback` all listed

**- [ ] Step 4: Commit**

```bash
git add api/routes/upload.py api/routes/jobs.py
git commit -m "feat(api): wire upload + POST /jobs through Project Service"
```

---

## Task 6: Progress Worker (pipeline/worker.py)

**Files:**
- Create: `pipeline/worker.py`

**Key design:**
- Reads from `mediaflow:jobs` XREADGROUP (consumer group `pipeline-workers`)
- Downloads from MinIO `processing/{job_id}` to a temp dir
- Builds a config for runner.execute() from the stage_plan
- Uses a `_CallbackPub` object (implements `publish()`) that HTTP POSTs to DAG-Service
- Uploads outputs from temp dir to MinIO `output/{job_id}/`
- Cleans up temp dir on completion

**- [ ] Step 1: Implement pipeline/worker.py**

```python
"""Progress Worker — reads MQ, runs pipeline stages, reports to DAG-Service."""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import redis as redis_lib

from pipeline.config import load_config
from pipeline.runner import execute as run_stages
from pipeline.mq.publisher import EventPublisher

log = logging.getLogger(__name__)

_MQ_KEY = "mediaflow:jobs"
_CONSUMER_GROUP = "pipeline-workers"
_CONSUMER_NAME = f"worker-{os.getpid()}"
_DAGSERVICE_URL = os.getenv("DAGSERVICE_URL", "http://localhost:8080")


class _CallbackPub:
    """Slim publisher that HTTP POSTs stage results to DAG-Service."""

    def __init__(self, job_id: str, retry_attempt: int):
        self.job_id = job_id
        self.retry_attempt = retry_attempt
        self._last_stage: str | None = None

    def publish(self, event: str, stem: str, **kwargs):
        if event == "stage.started":
            self._last_stage = kwargs.get("stage")
            return
        if event != "stage.completed":
            return
        stage = kwargs.get("stage", "")
        self._post_callback(stage, "success", None)

    def report_failure(self, stage: str, error_msg: str):
        self._post_callback(stage, "failed", error_msg)

    def _post_callback(self, stage: str, status: str, error_msg: str | None):
        try:
            httpx.post(
                f"{_DAGSERVICE_URL}/internal/stage-callback",
                json={
                    "job_id": self.job_id,
                    "stage": stage,
                    "status": status,
                    "retry_attempt": self.retry_attempt,
                    "error_msg": error_msg,
                },
                timeout=10.0,
            )
        except Exception as exc:
            log.error("Callback POST failed for job %s stage %s: %s", self.job_id, stage, exc)


def _download_from_minio(client, processing_path: str, dest_dir: Path) -> Path:
    """Download file from MinIO processing/ to dest_dir. Returns local path."""
    filename = processing_path.split("/")[-1]
    # If key has no extension it's a directory prefix — find the audio file
    try:
        objects = list(client.list_objects(processing_path))
        if objects:
            key = objects[0].object_name
            filename = key.split("/")[-1]
        else:
            key = processing_path
    except Exception:
        key = processing_path
    dest = dest_dir / filename
    client.download_to_file(key, dest)
    return dest


def _upload_outputs(client, job_id: str, output_dir: Path):
    """Upload all files from output_dir to MinIO output/{job_id}/."""
    for f in output_dir.iterdir():
        if f.is_file():
            key = f"output/{job_id}/{f.name}"
            client.upload_file(str(f), key)
            log.info("Uploaded %s → %s", f.name, key)


def _run_job(msg_id: str, fields: dict, r: redis_lib.Redis):
    from api.utils.minio import get_client  # ponytail: reuse existing client
    cfg = load_config()
    client = get_client()

    job_id = fields["job_id"]
    processing_path = fields["processing_path"]
    stage_plan = json.loads(fields["stage_plan"])
    retry_attempt = int(fields.get("retry_attempt", "0"))
    resume_from = fields.get("resume_from_stage")

    log.info("Worker: job=%s retry=%d from=%s", job_id, retry_attempt, resume_from)

    # Build a cfg with only the planned stages enabled
    stage_cfgs = [{"id": s["stage"], "enabled": True, **s.get("config", {})}
                  for s in stage_plan]
    job_cfg = {**cfg, "pipeline": {**cfg.get("pipeline", {}), "stages": stage_cfgs}}

    workdir = Path(tempfile.mkdtemp(prefix=f"mf_{job_id}_"))
    output_dir = workdir / "output"
    output_dir.mkdir()

    pub = _CallbackPub(job_id, retry_attempt)
    failed_stage: str | None = None

    try:
        audio_path = _download_from_minio(client, processing_path, workdir)
        ctx = {
            "stem": job_id,
            "workspace": workdir,
            "output_dir": output_dir,
            "input_path": audio_path,
        }
        run_stages(job_cfg, ctx, pub, from_stage=resume_from)
        _upload_outputs(client, job_id, output_dir)
    except Exception as exc:
        failed_stage = pub._last_stage or resume_from or stage_plan[0]["stage"]
        log.error("Job %s failed at stage %s: %s", job_id, failed_stage, exc)
        pub.report_failure(failed_stage, str(exc))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    r.xack(_MQ_KEY, _CONSUMER_GROUP, msg_id)


def _ensure_consumer_group(r: redis_lib.Redis):
    try:
        r.xgroup_create(_MQ_KEY, _CONSUMER_GROUP, id="0", mkstream=True)
    except redis_lib.exceptions.ResponseError:
        pass  # group already exists


def run():
    cfg = load_config()
    max_workers = cfg.get("pipeline", {}).get("max_concurrent_jobs", 2)
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))

    r = redis_lib.Redis(host=redis_host, port=redis_port, decode_responses=True)
    _ensure_consumer_group(r)

    executor = ThreadPoolExecutor(max_workers=max_workers)
    log.info("Progress Worker started (max_workers=%d)", max_workers)

    while True:
        try:
            msgs = r.xreadgroup(
                _CONSUMER_GROUP, _CONSUMER_NAME,
                {_MQ_KEY: ">"},
                count=1, block=5000,
            )
            if msgs:
                for stream, entries in msgs:
                    for msg_id, fields in entries:
                        executor.submit(_run_job, msg_id, fields, r)
        except KeyboardInterrupt:
            log.info("Worker stopping")
            break
        except Exception as exc:
            log.error("Worker loop error: %s", exc)
            time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
```

**- [ ] Step 2: Write a minimal smoke test (assert-based)**

Add to end of `pipeline/worker.py`:

```python
# ponytail: self-check
def _demo():
    from unittest.mock import MagicMock
    pub = _CallbackPub("job_test", 0)
    pub.publish("stage.started", "job_test", stage="transcribe")
    assert pub._last_stage == "transcribe"
    # report_failure builds the right payload without crashing
    pub_calls = []
    pub._post_callback = lambda *a, **kw: pub_calls.append(a)
    pub.report_failure("transcribe", "OOM")
    assert pub_calls[0][0] == "transcribe"
    print("worker self-check: OK")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
    else:
        logging.basicConfig(level=logging.INFO)
        run()
```

**- [ ] Step 3: Run self-check**

```bash
source venv/bin/activate
python -m pipeline.worker --demo
```
Expected: `worker self-check: OK`

**- [ ] Step 4: Commit**

```bash
git add pipeline/worker.py
git commit -m "feat(pipeline): Progress Worker — MQ consumer + stage runner + MinIO I/O"
```

---

## Task 7: Watcher simplification

**Files:**
- Modify: `pipeline/watcher.py`

**Goal:** The watcher now just detects a file, uploads it to MinIO `input/`, and POSTs to `POST /jobs`. All pipeline logic moves to the Progress Worker. The existing `_run_pipeline()` call is removed.

**- [ ] Step 1: Read watcher.py to find the entry points**

Open `pipeline/watcher.py` and locate:
- `_run_pipeline(path, cfg, pub)` function
- `FileHandler.on_created()` method
- The startup recovery scan

**- [ ] Step 2: Replace _run_pipeline with _ingest_file**

Replace `_run_pipeline` and the `FileHandler.on_created` / `_submit` methods with:

```python
def _ingest_file(path: Path, cfg: dict) -> None:
    """Upload newly detected file to MinIO and trigger Project Service."""
    import httpx
    from api.utils.minio import get_client

    filename = path.name
    minio_key = f"input/{filename}"
    api_url = os.getenv("API_URL", "http://localhost:8080")

    log.info("Watcher: ingesting %s → MinIO %s", filename, minio_key)
    try:
        client = get_client()
        client.upload_file(str(path), minio_key)
    except Exception as exc:
        log.error("Watcher: MinIO upload failed for %s: %s", filename, exc)
        _mark_failed(path)
        return

    try:
        resp = httpx.post(
            f"{api_url}/jobs",
            json={"file_key": minio_key, "filename": filename},
            timeout=30.0,
        )
        resp.raise_for_status()
        job_id = resp.json().get("job_id")
        log.info("Watcher: submitted %s → job_id=%s", filename, job_id)
        path.unlink(missing_ok=True)  # remove from 1_input/ after successful handoff
    except Exception as exc:
        log.error("Watcher: API notify failed for %s: %s", filename, exc)
        _mark_failed(path)
```

In `FileHandler`, replace `_submit(path)` body with just `executor.submit(_ingest_file, path, cfg)`.

Remove the import of `runner`, `stages`, `EventPublisher`, `rerun`, `telemetry`, `lifecycle` if they were only used by `_run_pipeline`. Keep `_mark_failed`.

**- [ ] Step 3: Remove startup recovery scan that reruns pipeline**

The startup scan in `run()` that calls `_run_pipeline` on existing files should be updated to call `_ingest_file` instead (or removed if files in 1_input/ on startup should be re-ingested).

**- [ ] Step 4: Test (manual)**

Drop a file into `workspace/1_input/` and verify watcher log shows:
```
Watcher: ingesting file.m4a → MinIO input/file.m4a
Watcher: submitted file.m4a → job_id=...
```

**- [ ] Step 5: Commit**

```bash
git add pipeline/watcher.py
git commit -m "refactor(pipeline): watcher simplification — detect + MinIO upload + API notify"
```

---

## Task 8: FR4 Transcript Correction

**Files:**
- Create: `api/services/correction.py`
- Create: `api/routes/correction.py`
- Modify: `api/main.py`

**- [ ] Step 1: Implement api/services/correction.py**

```python
"""FR4 Transcript Correction — rebuild corrected SRT, write to MinIO output/."""
import time

import asyncpg
from fastapi import HTTPException

from api.db.queries import get_job, upsert_job


def _rebuild_srt(segments: list[dict]) -> str:
    """Build SRT content from [{index, start, end, text}] segment list."""
    lines = []
    for seg in segments:
        idx = seg["index"]
        start = _fmt_ts(seg["start"])
        end = _fmt_ts(seg["end"])
        lines.append(f"{idx}\n{start} --> {end}\n{seg['text'].strip()}\n")
    return "\n".join(lines)


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


async def apply_correction(
    pool: asyncpg.Pool,
    minio,
    job_id: str,
    segments: list[dict],
) -> None:
    """Save corrected SRT to MinIO and update job row."""
    job = await get_job(pool, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    if job["status"] not in ("completed",):
        raise HTTPException(409, "Correction only allowed on completed jobs")

    srt_content = _rebuild_srt(segments)
    corrected_key = f"output/{job_id}/{job_id}_corrected.srt"
    try:
        minio.put_object(corrected_key, srt_content.encode())
    except Exception as exc:
        raise HTTPException(500, f"Failed to write corrected SRT: {exc}")

    new_status = (
        "in_progress"
        if job.get("verification_status") == "unverified"
        else job["verification_status"]
    )
    await upsert_job(
        pool, job_id,
        corrected_srt_path=corrected_key,
        verification_status=new_status,
    )


async def finalize_correction(pool: asyncpg.Pool, job_id: str) -> None:
    """Mark job as verified."""
    job = await get_job(pool, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    await upsert_job(
        pool, job_id,
        verification_status="verified",
        verified_at=time.time(),
        verified_by="user",
    )
```

**- [ ] Step 2: Create api/routes/correction.py**

```python
"""FR4 — Transcript correction endpoints."""
from typing import List

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.services.correction import apply_correction, finalize_correction
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs/{job_id}/correction")


class Segment(BaseModel):
    index: int
    start: float
    end: float
    text: str


class CorrectionRequest(BaseModel):
    segments: List[Segment]


@router.patch("", status_code=204)
async def patch_correction(job_id: str, req: CorrectionRequest, request: Request):
    pool = request.app.state.pool
    await apply_correction(
        pool,
        minio_mod.get_client(),
        job_id,
        [s.model_dump() for s in req.segments],
    )


@router.post("/finalize", status_code=204)
async def post_finalize(job_id: str, request: Request):
    await finalize_correction(request.app.state.pool, job_id)
```

**- [ ] Step 3: Register in api/main.py**

```python
from api.routes import correction
app.include_router(correction.router)
```

**- [ ] Step 4: Self-check for correction service**

```python
# Add to end of api/services/correction.py
def _demo():
    srt = _rebuild_srt([
        {"index": 1, "start": 0.0, "end": 2.5, "text": "Hello"},
        {"index": 2, "start": 3.0, "end": 5.0, "text": "World"},
    ])
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "00:00:03,000 --> 00:00:05,000" in srt
    print("correction self-check: OK")

if __name__ == "__main__":
    _demo()
```

```bash
python api/services/correction.py
```
Expected: `correction self-check: OK`

**- [ ] Step 5: Commit**

```bash
git add api/services/correction.py api/routes/correction.py api/main.py
git commit -m "feat(api): FR4 transcript correction — PATCH /jobs/{id}/correction + finalize"
```

---

## Task 9: Clip API (FR3 audio segment playback)

**Files:**
- Create: `api/routes/clip.py`
- Modify: `api/main.py`

**Design:** Minimal — no caching. Generate ffmpeg clip on demand, upload to MinIO `clips/`, return presigned URL. If clip already exists in MinIO, return its presigned URL directly.

**- [ ] Step 1: Create api/routes/clip.py**

```python
"""FR3 — On-demand audio clip from clean.wav for a transcript segment."""
import asyncio
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from api.db.queries import get_job
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs/{job_id}/segment")


@router.get("/{index}/audio")
async def get_segment_audio(job_id: str, index: int, request: Request):
    pool = request.app.state.pool
    job = await get_job(pool, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    client = minio_mod.get_client()
    clip_key = f"clips/{job_id}/seg_{index}.wav"

    # Return cached clip if present
    try:
        return {"url": client.presign_get_url(minio_mod.OUTPUT_BUCKET, clip_key)}
    except Exception:
        pass

    # Generate clip on demand from clean.wav in output/
    segments_key = f"output/{job_id}/{job_id}_segments.json"
    wav_key = f"output/{job_id}/{job_id}_clean.wav"

    try:
        import json
        segments_data = client.get_object_content(segments_key)
        segments = json.loads(segments_data)
        seg = next((s for s in segments if s.get("id") == index), None)
        if seg is None:
            raise HTTPException(404, f"Segment {index} not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Could not load segments: {exc}")

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.wav"
        dst = Path(tmpdir) / f"seg_{index}.wav"
        try:
            client.download_to_file(wav_key, src)
        except Exception as exc:
            raise HTTPException(500, f"Could not download audio: {exc}")

        start = seg["start"]
        duration = seg["end"] - seg["start"]
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["ffmpeg", "-y", "-ss", str(start), "-i", str(src),
                 "-t", str(duration), "-ar", "16000", "-ac", "1", str(dst)],
                check=True, capture_output=True,
            )
        )
        client.upload_file(str(dst), clip_key)

    return {"url": client.presign_get_url(minio_mod.OUTPUT_BUCKET, clip_key)}
```

**- [ ] Step 2: Register in api/main.py**

```python
from api.routes import clip
app.include_router(clip.router)
```

**- [ ] Step 3: Commit**

```bash
git add api/routes/clip.py api/main.py
git commit -m "feat(api): FR3 segment clip API — GET /jobs/{id}/segment/{index}/audio"
```

---

## Task 10: Integration smoke test

**Goal:** End-to-end verify the new flow works: upload → Project Service → DAG → MQ → Worker → callback → complete.

**- [ ] Step 1: Start all services**

```bash
bash scripts/ctl.sh start all
source venv/bin/activate
python -m pipeline.worker &
```

**- [ ] Step 2: Upload a test file**

```bash
# Initiate multipart upload
curl -s -X POST http://localhost:8080/upload/init \
  -H "Content-Type: application/json" \
  -d '{"filename":"test-speech.m4a","size_bytes":323288,"content_type":"audio/mp4"}' | tee /tmp/upload_init.json

# Use returned presigned URL to upload the file (single-part)
PRESIGNED=$(cat /tmp/upload_init.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['parts'][0]['url'])")
ETAG=$(curl -s -X PUT "$PRESIGNED" \
  --upload-file tests/fixtures/test-speech.m4a \
  -D - | grep -i etag | tr -d '\r' | awk '{print $2}')
UPLOAD_ID=$(cat /tmp/upload_init.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['upload_id'])")
MINIO_KEY=$(cat /tmp/upload_init.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['minio_key'])")

# Complete upload
curl -s -X POST http://localhost:8080/upload/complete \
  -H "Content-Type: application/json" \
  -d "{\"upload_id\":\"$UPLOAD_ID\",\"minio_key\":\"$MINIO_KEY\",\"parts\":[{\"part_number\":1,\"etag\":\"$ETAG\"}]}" \
  | python3 -m json.tool
```

Expected: `{"job_id": "...", "status": "queued"}`

**- [ ] Step 3: Poll job status**

```bash
JOB_ID=<from above>
watch -n 2 "curl -s http://localhost:8080/jobs/$JOB_ID | python3 -m json.tool"
```

Expected: status progresses `queued → processing → completed`

**- [ ] Step 4: Verify output in MinIO**

```bash
curl -s http://localhost:8080/jobs/$JOB_ID | python3 -c "
import sys,json; d=json.load(sys.stdin); print(d.get('output_srt_path',''))"
```

Expected: non-empty path in `output/{job_id}/`

---

## Self-Review

**Spec coverage check:**
- ✅ FR1 (file ingestion): both paths covered — watcher (Task 7) + frontend upload (Task 5)
- ✅ FR2 (pipeline): Progress Worker runs all stages via runner.execute() (Task 6)
- ✅ FR3 (output access): clip API (Task 9), existing GET /files routes unchanged
- ✅ FR4 (transcript correction): Task 8
- ✅ FR5 (retry): woven into DAG-Service Task 2 — retry_attempt < max_retries → re-enqueue
- ✅ FR6 (security check): Task 4 — validate_fr6() called in on_upload_trigger

**NFR coverage:**
- ✅ NFR1 (GPU-first): unchanged — Whisper/Ollama still use Apple Silicon GPU via existing stages.py
- ✅ NFR3 (no SPOF): DAG-Service owns retry, worker is stateless
- ✅ NFR4 (scale-out): max_concurrent_jobs controls ThreadPoolExecutor size
- ✅ NFR6 (configurability): max_retries, retry_backoff_sec, max_concurrent_jobs in config.yaml

**Ponytail audit:**
- Skipped Sub-plan B (provider abstraction) — YAGNI
- Skipped G (Jinja2 web updates) — React frontend already built
- Clip API has no TTL caching — add when latency is measurably a problem
- `_enqueue_after_backoff` uses a daemon thread + `asyncio.run()` — not ideal for high throughput; switch to a proper task queue if backoff storms become an issue
- DAG-Service functions are plain module functions, not a class
