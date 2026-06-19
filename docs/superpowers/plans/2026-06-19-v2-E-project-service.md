# Sub-plan E: Project Service + FR6

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the formal job intake layer — `POST /jobs` accepts a MinIO input key + optional dag_flow, runs FR6 security checks (filename anomaly + size validation), copies file to `processing/`, creates the job row, and calls `trigger_job()` from the DAG-Service.

**Architecture:** `api/services/project.py` owns the business logic. `api/routes/jobs.py` (created in Sub-plan C) gains the `POST /jobs` route. FR6 validation is a pure function — easy to test and to extend later.

**Tech Stack:** FastAPI, asyncpg, boto3 (for MinIO head_object to verify file exists + size)

**Depends on:** Sub-plan A (PostgreSQL), Sub-plan C (DAG-Service trigger_job + jobs route)

## Global Constraints

- FR6 Phase 1: filename anomaly (reject path traversal, null bytes, excessively long names) + file size (0 or > `UPLOAD_MAX_FILE_BYTES`)
- MinIO file-existence check: `head_object` on `input/` bucket — verifies upload completed before job creation
- If FR6 fails: delete file from `processing/` (if copied), do NOT create job row, return HTTP 400 with reason
- `submitted_by` defaults to `"anonymous"` — no auth in Phase 1
- `dag_flow` in request body is optional; None → DAG-Service picks default flow

---

## File Structure

```
# Create
api/services/project.py         — job intake logic + FR6 validation
tests/test_project_service.py   — unit tests (mock pool, mock MinIO, mock dag.trigger_job)

# Modify
api/routes/jobs.py              — add POST /jobs route (file already exists from Sub-plan C)
tests/test_jobs_route.py        — add POST /jobs tests
```

---

## Interfaces

**Produces:**
```python
# api/services/project.py
def validate_fr6(filename: str, size_bytes: int, max_bytes: int) -> tuple[bool, str]:
    """Returns (ok, reason). Pure function — no I/O."""

async def create_job(
    pool: asyncpg.Pool,
    redis,
    minio_client,     # api.minio_client.MinIOClient
    input_key: str,   # e.g. "input/abc123_lesson01.m4a"
    dag_flow: str | None,
    max_file_bytes: int,
) -> str:             # returns job_id
    """Verify → FR6 → copy → create job → trigger. Raises ValueError on FR6 failure."""
```

**POST /jobs request/response:**
```python
# Request body
{"file_key": "input/abc123_lesson01.m4a", "dag_flow": "course-v1"}
# or
{"file_key": "input/abc123_lesson01.m4a"}  # dag_flow omitted → use default

# Success response (201)
{"job_id": "abc123", "status": "queued"}

# FR6 failure response (400)
{"detail": "FR6: filename contains path traversal characters"}

# File not found in MinIO (404)
{"detail": "File not found in MinIO input bucket"}
```

---

## Task 1: FR6 Validation (Pure Function)

**Files:**
- Create: `api/services/project.py`
- Create: `tests/test_project_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_project_service.py
import pytest
from api.services.project import validate_fr6

MAX = 5 * 1024 * 1024 * 1024  # 5 GB

def test_valid_filename_passes():
    ok, reason = validate_fr6("lesson01.m4a", 1024 * 1024, MAX)
    assert ok is True
    assert reason == ""

def test_empty_file_fails():
    ok, reason = validate_fr6("lesson01.m4a", 0, MAX)
    assert ok is False
    assert "empty" in reason.lower()

def test_oversized_file_fails():
    ok, reason = validate_fr6("lesson01.m4a", MAX + 1, MAX)
    assert ok is False
    assert "size" in reason.lower()

def test_path_traversal_fails():
    ok, reason = validate_fr6("../../../etc/passwd", 1024, MAX)
    assert ok is False
    assert "traversal" in reason.lower() or "path" in reason.lower()

def test_null_byte_in_name_fails():
    ok, reason = validate_fr6("lesson\x00.m4a", 1024, MAX)
    assert ok is False
    assert "null" in reason.lower() or "character" in reason.lower()

def test_very_long_filename_fails():
    ok, reason = validate_fr6("a" * 256 + ".m4a", 1024, MAX)
    assert ok is False
    assert "long" in reason.lower() or "length" in reason.lower()

def test_chinese_filename_passes():
    ok, reason = validate_fr6("第一課_錄音.m4a", 1024 * 1024, MAX)
    assert ok is True

def test_unsupported_extension_fails():
    ok, reason = validate_fr6("lesson.exe", 1024, MAX)
    assert ok is False
    assert "extension" in reason.lower() or "format" in reason.lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_project_service.py -v
```

Expected: `ImportError: cannot import name 'validate_fr6'`

- [ ] **Step 3: Write validate_fr6 in api/services/project.py**

```python
# api/services/project.py
import os
import re
import uuid
import time
import logging
from pathlib import PurePosixPath

import asyncpg

from api import db
from api.services import dag

log = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {".mp4", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".webm"}
_MAX_FILENAME_LEN   = 255


def validate_fr6(filename: str, size_bytes: int, max_bytes: int) -> tuple[bool, str]:
    """FR6 Phase 1: filename anomaly + size check. Returns (ok, reason)."""
    # Null bytes
    if "\x00" in filename:
        return False, "FR6: filename contains null character"

    # Path traversal
    parts = PurePosixPath(filename).parts
    if any(p in ("..", ".") or p.startswith("/") for p in parts):
        return False, "FR6: filename contains path traversal characters"
    if "\\" in filename or "/" in filename:
        return False, "FR6: filename must not contain directory separators"

    # Length
    if len(filename) > _MAX_FILENAME_LEN:
        return False, f"FR6: filename too long ({len(filename)} > {_MAX_FILENAME_LEN})"

    # Extension
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        return False, f"FR6: unsupported file extension {suffix!r}"

    # Size
    if size_bytes == 0:
        return False, "FR6: file is empty"
    if size_bytes > max_bytes:
        return False, f"FR6: file size {size_bytes} exceeds limit {max_bytes}"

    return True, ""
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_project_service.py -v
```

Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/project.py tests/test_project_service.py
git commit -m "feat(project): validate_fr6 — filename anomaly + size + extension checks"
```

---

## Task 2: create_job — Full Intake Flow

**Files:**
- Modify: `api/services/project.py`
- Modify: `tests/test_project_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_project_service.py — add

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute  = AsyncMock()
    return pool

@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.xadd = AsyncMock(return_value="1234-0")
    return r

@pytest.fixture
def mock_minio():
    m = MagicMock()
    m.input_bucket = "mediaflow-input"
    m._s3 = MagicMock()
    m._s3.head_object.return_value = {"ContentLength": 1024 * 1024}
    m.copy_input_to_processing.return_value = "processing/job1/lesson01.m4a"
    return m

@pytest.mark.asyncio
async def test_create_job_happy_path(mock_pool, mock_redis, mock_minio):
    from api.services.project import create_job
    mock_pool.fetchrow.return_value = {
        "id": "general-v1", "stage_plan": [{"stage": "preprocess", "config": {}}],
        "is_default": True, "deprecated": False, "created_at": 1000.0,
    }

    with patch("api.services.dag.trigger_job", AsyncMock()):
        job_id = await create_job(
            pool=mock_pool, redis=mock_redis, minio_client=mock_minio,
            input_key="input/lesson01.m4a", dag_flow=None, max_file_bytes=5*1024**3,
        )
    assert isinstance(job_id, str)
    assert len(job_id) > 0
    assert mock_minio.copy_input_to_processing.called

@pytest.mark.asyncio
async def test_create_job_file_not_in_minio_raises(mock_pool, mock_redis, mock_minio):
    from api.services.project import create_job
    from botocore.exceptions import ClientError
    mock_minio._s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    with pytest.raises(FileNotFoundError, match="MinIO"):
        await create_job(
            pool=mock_pool, redis=mock_redis, minio_client=mock_minio,
            input_key="input/missing.m4a", dag_flow=None, max_file_bytes=5*1024**3,
        )

@pytest.mark.asyncio
async def test_create_job_fr6_failure_raises(mock_pool, mock_redis, mock_minio):
    from api.services.project import create_job
    mock_minio._s3.head_object.return_value = {"ContentLength": 0}  # empty file
    with pytest.raises(ValueError, match="FR6"):
        await create_job(
            pool=mock_pool, redis=mock_redis, minio_client=mock_minio,
            input_key="input/empty.m4a", dag_flow=None, max_file_bytes=5*1024**3,
        )
    # Ensure no processing copy was made
    assert not mock_minio.copy_input_to_processing.called
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_project_service.py::test_create_job_happy_path -v
```

Expected: FAIL — `ImportError` or `AttributeError`

- [ ] **Step 3: Implement create_job in api/services/project.py**

```python
# append to api/services/project.py
import uuid
from botocore.exceptions import ClientError

async def create_job(
    pool: asyncpg.Pool,
    redis,
    minio_client,
    input_key: str,
    dag_flow: str | None,
    max_file_bytes: int,
) -> str:
    filename = input_key.split("/")[-1]

    # Verify file exists in MinIO input/ and get size
    try:
        head = minio_client._s3.head_object(
            Bucket=minio_client.input_bucket, Key=input_key
        )
    except ClientError as e:
        raise FileNotFoundError(
            f"File not found in MinIO input bucket: {input_key!r}"
        ) from e

    size_bytes = head["ContentLength"]

    # FR6 check
    ok, reason = validate_fr6(filename, size_bytes, max_file_bytes)
    if not ok:
        raise ValueError(reason)

    job_id = str(uuid.uuid4())

    # Copy input → processing (7d TTL bucket — safe from 1h input TTL)
    processing_key = minio_client.copy_input_to_processing(input_key, job_id)

    # Create job row
    await db.upsert_job(
        pool, job_id,
        filename=filename,
        submitted_by="anonymous",
        status="submitted",
        submitted_at=time.time(),
        minio_input_key=input_key,
        minio_processing_key=processing_key,
    )

    # Trigger DAG
    await dag.trigger_job(pool, redis, job_id, dag_flow, processing_key)

    log.info("Job %s created (file=%s, flow=%s)", job_id, filename, dag_flow)
    return job_id
```

- [ ] **Step 4: Run all project service tests**

```bash
pytest tests/test_project_service.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/project.py tests/test_project_service.py
git commit -m "feat(project): create_job — verify MinIO → FR6 → copy → create → trigger"
```

---

## Task 3: POST /jobs Route

**Files:**
- Modify: `api/routes/jobs.py`
- Modify: `tests/test_jobs_route.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jobs_route.py — add
import os

def test_post_job_creates_job_and_returns_201(client):
    with patch("api.services.project.create_job", AsyncMock(return_value="job-abc")) as mock_create:
        resp = client.post("/jobs", json={
            "file_key": "input/lesson01.m4a",
            "dag_flow": "course-v1"
        })
    assert resp.status_code == 201
    assert resp.json()["job_id"] == "job-abc"
    assert resp.json()["status"] == "queued"
    assert mock_create.called

def test_post_job_fr6_failure_returns_400(client):
    with patch("api.services.project.create_job",
               AsyncMock(side_effect=ValueError("FR6: file is empty"))):
        resp = client.post("/jobs", json={"file_key": "input/empty.m4a"})
    assert resp.status_code == 400
    assert "FR6" in resp.json()["detail"]

def test_post_job_file_not_found_returns_404(client):
    with patch("api.services.project.create_job",
               AsyncMock(side_effect=FileNotFoundError("File not found in MinIO input bucket"))):
        resp = client.post("/jobs", json={"file_key": "input/missing.m4a"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_jobs_route.py::test_post_job_creates_job_and_returns_201 -v
```

Expected: FAIL — 405 Method Not Allowed (POST not implemented)

- [ ] **Step 3: Add POST /jobs to api/routes/jobs.py**

```python
# api/routes/jobs.py — add imports and endpoint
import os
from pydantic import BaseModel
from fastapi import HTTPException

from api.services import project as project_svc
from api import minio_client as minio_mod

UPLOAD_MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(5 * 1024**3)))


class CreateJobBody(BaseModel):
    file_key: str
    dag_flow: str | None = None


@router.post("", status_code=201)
async def create_job(body: CreateJobBody, request: Request):
    pool   = request.app.state.pool
    redis  = request.app.state.redis
    minio  = minio_mod.get_client()
    try:
        job_id = await project_svc.create_job(
            pool=pool, redis=redis, minio_client=minio,
            input_key=body.file_key, dag_flow=body.dag_flow,
            max_file_bytes=UPLOAD_MAX_FILE_BYTES,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"job_id": job_id, "status": "queued"}
```

- [ ] **Step 4: Run all jobs route tests**

```bash
pytest tests/test_jobs_route.py -v
```

Expected: all PASS.

- [ ] **Step 5: End-to-end smoke test**

```bash
# Prerequisite: docker compose up -d; uvicorn api.main:app --port 8080
# Upload a file to MinIO input/ first:
python3 -c "
import boto3
s3 = boto3.client('s3', endpoint_url='http://localhost:9000',
                  aws_access_key_id='mediaflow', aws_secret_access_key='changeme',
                  region_name='us-east-1')
s3.upload_file('tests/fixtures/test-speech.m4a', 'mediaflow-input', 'input/test-speech.m4a')
print('uploaded')
"

curl -s -X POST http://localhost:8080/jobs \
  -H "Content-Type: application/json" \
  -d '{"file_key":"input/test-speech.m4a"}' | python3 -m json.tool
```

Expected:
```json
{"job_id": "...", "status": "queued"}
```

- [ ] **Step 6: Commit**

```bash
git add api/routes/jobs.py tests/test_jobs_route.py
git commit -m "feat(api): POST /jobs — job intake with FR6 validation"
```

---

## Self-Review Checklist

- [ ] `validate_fr6("../etc/passwd", 1024, MAX)` returns `(False, reason)` — path traversal blocked
- [ ] `validate_fr6("lesson.exe", 1024, MAX)` returns `(False, reason)` — extension blocked
- [ ] `create_job` with FR6 failure does NOT call `copy_input_to_processing` — no orphaned processing/ files
- [ ] `create_job` with missing MinIO file raises `FileNotFoundError` (not `ValueError`)
- [ ] `POST /jobs` returns HTTP 201, not 200
- [ ] `POST /jobs` with `dag_flow` omitted passes `None` to `create_job` → DAG-Service picks default flow
- [ ] No job row is written to DB if FR6 check fails
