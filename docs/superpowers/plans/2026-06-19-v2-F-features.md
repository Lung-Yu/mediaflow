# Sub-plan F: FR4 Transcript Correction + Segment Clip API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the two remaining user-facing features — FR4 (inline transcript correction with verification flow) and the segment audio clip API (on-demand ffmpeg clip with MinIO `clips/` caching).

**Architecture:** Two new route files (`api/routes/correction.py`, `api/routes/clip.py`). Correction logic lives in `api/services/correction.py`. Clip logic is small enough to live directly in the route. Both write to MinIO `output/`; clips also write to `clips/` (10-min TTL).

**Tech Stack:** FastAPI, asyncpg, boto3, httpx (for MinIO presigned), subprocess (ffmpeg for clipping)

**Depends on:** Sub-plan A (jobs table, corrected_srt_path, verification_status), Sub-plan C (GET /jobs/{id}), Sub-plan D (MinIO output/ contains _segments.json + _clean.wav)

## Global Constraints

- FR4 `PATCH /jobs/{id}/correction` accepts full segment array replacement — not diff/patch
- First `PATCH` auto-transitions `verification_status` from `unverified` → `in_progress`
- `POST /jobs/{id}/correction/finalize` is valid from either `unverified` or `in_progress`
- Clips are cached in MinIO `clips/` with a short TTL — missing clip = generate on demand (never 404 for a valid segment index)
- ffmpeg clip is run in `asyncio.run_in_executor` — never blocks the event loop

---

## File Structure

```
# Create
api/services/correction.py      — rebuild_corrected_srt(), write to MinIO output/
api/routes/correction.py        — PATCH /jobs/{id}/correction, POST .../finalize
api/routes/clip.py              — GET /jobs/{id}/segment/{index}/audio

# Modify
api/main.py                     — include correction and clip routers
api/routes/jobs.py              — add GET /jobs/{id}/events

# Test
tests/test_correction.py        — correction service + routes
tests/test_clip_route.py        — segment clip route
```

---

## Interfaces

**Consumes (from Sub-plan D output in MinIO):**
```
output/{job_id}/{stem}_segments.json  — [{id, start, end, text, ...}]
output/{job_id}/{stem}_clean.wav      — full audio (source for clips)
```

**Produces:**
```python
# api/services/correction.py
async def apply_correction(
    pool: asyncpg.Pool,
    minio_client,
    job_id: str,
    segments: list[dict],   # [{index: int, text: str}]
) -> str:                   # corrected SRT content

async def finalize_verification(
    pool: asyncpg.Pool,
    job_id: str,
) -> None:

# PATCH /jobs/{id}/correction — request body
{"segments": [{"index": 0, "text": "你好"}, {"index": 2, "text": "再見"}]}

# POST /jobs/{id}/correction/finalize — no body
# GET /jobs/{id}/segment/{index}/audio → {"url": "https://...", "expires_in": 600}
```

---

## Task 1: Correction Service

**Files:**
- Create: `api/services/correction.py`
- Create: `tests/test_correction.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_correction.py
import json, pytest
from unittest.mock import AsyncMock, MagicMock, patch

SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 2.0, "text": "你好"},
    {"id": 1, "start": 2.1, "end": 4.0, "text": "我是老師"},
    {"id": 2, "start": 4.1, "end": 6.0, "text": "再見"},
]

@pytest.fixture
def mock_pool():
    p = MagicMock()
    p.fetchrow = AsyncMock()
    p.execute  = AsyncMock()
    return p

@pytest.fixture
def mock_minio():
    m = MagicMock()
    m.output_bucket = "mediaflow-output"
    m._s3 = MagicMock()
    m._s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(SEGMENTS).encode()))
    }
    m._s3.put_object = MagicMock()
    return m

@pytest.mark.asyncio
async def test_apply_correction_produces_valid_srt(mock_pool, mock_minio):
    from api.services.correction import apply_correction
    mock_pool.fetchrow.return_value = {
        "id": "job1", "filename": "lesson01.m4a",
        "verification_status": "unverified",
        "corrected_srt_path": None,
    }

    edits = [{"index": 0, "text": "你好啊"}]  # correct segment 0
    with patch("api.db.get_job", AsyncMock(return_value=mock_pool.fetchrow.return_value)), \
         patch("api.db.upsert_job", AsyncMock()):
        srt = await apply_correction(mock_pool, mock_minio, "job1", edits)

    # SRT should contain edited text
    assert "你好啊" in srt
    # SRT should contain unedited segments too
    assert "我是老師" in srt
    assert "再見" in srt
    # put_object must have been called to write corrected SRT to MinIO
    assert mock_minio._s3.put_object.called

@pytest.mark.asyncio
async def test_apply_correction_sets_in_progress_from_unverified(mock_pool, mock_minio):
    from api.services.correction import apply_correction
    job = {"id": "job1", "filename": "lesson01.m4a",
           "verification_status": "unverified", "corrected_srt_path": None}
    upsert_calls = []
    with patch("api.db.get_job", AsyncMock(return_value=job)), \
         patch("api.db.upsert_job", AsyncMock(side_effect=lambda pool, jid, **kw: upsert_calls.append(kw))):
        await apply_correction(mock_pool, mock_minio, "job1", [{"index": 0, "text": "test"}])

    # verification_status must have been set to in_progress
    vs_calls = [c for c in upsert_calls if "verification_status" in c]
    assert any(c["verification_status"] == "in_progress" for c in vs_calls)

@pytest.mark.asyncio
async def test_finalize_sets_verified(mock_pool):
    from api.services.correction import finalize_verification
    import time
    upsert_calls = []
    with patch("api.db.upsert_job", AsyncMock(side_effect=lambda pool, jid, **kw: upsert_calls.append(kw))):
        await finalize_verification(mock_pool, "job1")
    assert any(c.get("verification_status") == "verified" for c in upsert_calls)
    assert any("verified_at" in c for c in upsert_calls)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_correction.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Write api/services/correction.py**

```python
# api/services/correction.py
import io
import json
import time
from datetime import timedelta

import asyncpg

from api import db


def _seconds_to_srt_ts(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    total_s = int(td.total_seconds())
    ms = int((seconds - int(seconds)) * 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _seconds_to_srt_ts(seg["start"])
        end   = _seconds_to_srt_ts(seg["end"])
        text  = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


async def apply_correction(
    pool: asyncpg.Pool,
    minio_client,
    job_id: str,
    edits: list[dict],   # [{index: int, text: str}]
) -> str:
    job = await db.get_job(pool, job_id)
    if not job:
        raise ValueError(f"Job {job_id!r} not found")

    stem = job["filename"].rsplit(".", 1)[0]

    # Load segments from MinIO output/
    obj = minio_client._s3.get_object(
        Bucket=minio_client.output_bucket,
        Key=f"output/{job_id}/{stem}_segments.json",
    )
    segments: list[dict] = json.loads(obj["Body"].read())

    # Apply edits (full text replacement per index)
    edit_map = {e["index"]: e["text"] for e in edits}
    for seg in segments:
        if seg["id"] in edit_map:
            seg["text"] = edit_map[seg["id"]]

    corrected_srt = _build_srt(segments)

    # Write corrected SRT to MinIO output/
    corrected_key = f"output/{job_id}/{stem}_corrected.srt"
    minio_client._s3.put_object(
        Bucket=minio_client.output_bucket,
        Key=corrected_key,
        Body=corrected_srt.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )

    # Update job row
    updates: dict = {"corrected_srt_path": corrected_key}
    if job["verification_status"] == "unverified":
        updates["verification_status"] = "in_progress"
    await db.upsert_job(pool, job_id, **updates)

    return corrected_srt


async def finalize_verification(pool: asyncpg.Pool, job_id: str) -> None:
    await db.upsert_job(pool, job_id,
                        verification_status="verified",
                        verified_at=time.time())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_correction.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/correction.py tests/test_correction.py
git commit -m "feat(correction): apply_correction + finalize_verification service"
```

---

## Task 2: Correction Routes

**Files:**
- Create: `api/routes/correction.py`
- Modify: `api/main.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_correction.py — add route tests
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)

def test_patch_correction_returns_200(client):
    with patch("api.services.correction.apply_correction", AsyncMock(return_value="1\n...")):
        resp = client.patch("/jobs/job1/correction",
                            json={"segments": [{"index": 0, "text": "你好"}]})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

def test_finalize_returns_200(client):
    with patch("api.services.correction.finalize_verification", AsyncMock()):
        resp = client.post("/jobs/job1/correction/finalize")
    assert resp.status_code == 200

def test_patch_correction_invalid_body_returns_422(client):
    resp = client.patch("/jobs/job1/correction", json={"bad_field": 1})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_correction.py::test_patch_correction_returns_200 -v
```

Expected: FAIL — 404

- [ ] **Step 3: Write api/routes/correction.py**

```python
# api/routes/correction.py
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.services import correction as correction_svc
from api import minio_client as minio_mod

router = APIRouter(prefix="/jobs/{job_id}/correction", tags=["correction"])


class SegmentEdit(BaseModel):
    index: int
    text: str

class CorrectionBody(BaseModel):
    segments: list[SegmentEdit]


@router.patch("")
async def patch_correction(job_id: str, body: CorrectionBody, request: Request):
    pool  = request.app.state.pool
    minio = minio_mod.get_client()
    try:
        await correction_svc.apply_correction(
            pool, minio, job_id, [s.model_dump() for s in body.segments]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.post("/finalize")
async def finalize_correction(job_id: str, request: Request):
    pool = request.app.state.pool
    await correction_svc.finalize_verification(pool, job_id)
    return {"ok": True}
```

- [ ] **Step 4: Register router in api/main.py**

```python
from api.routes import correction
app.include_router(correction.router)
```

- [ ] **Step 5: Run all correction tests**

```bash
pytest tests/test_correction.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/correction.py api/main.py tests/test_correction.py
git commit -m "feat(api): PATCH /jobs/{id}/correction + POST .../finalize"
```

---

## Task 3: Segment Audio Clip Route

**Files:**
- Create: `api/routes/clip.py`
- Modify: `api/main.py`
- Create: `tests/test_clip_route.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_clip_route.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 2.5, "text": "你好"},
    {"id": 1, "start": 2.6, "end": 5.0, "text": "再見"},
]

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)

def _mock_minio_with_segments(segments):
    m = MagicMock()
    m.output_bucket = "mediaflow-output"
    m.clips_bucket  = "mediaflow-clips"
    m._s3 = MagicMock()
    m._s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(segments).encode()))
    }
    # Simulate clip already cached (head_object succeeds)
    m._s3.head_object.return_value = {"ContentLength": 50000}
    m.presign_get_url.return_value = "https://minio/clips/job1/0.wav?presign=abc"
    return m

def test_get_segment_audio_cached(client):
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    with patch("api.minio_client.get_client", return_value=mock_minio):
        resp = client.get("/jobs/job1/segment/0/audio")
    assert resp.status_code == 200
    assert "url" in resp.json()
    assert resp.json()["expires_in"] == 600

def test_get_segment_audio_cache_miss_generates_clip(client, tmp_path):
    from botocore.exceptions import ClientError
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    # head_object raises → cache miss
    mock_minio._s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": ""}}, "HeadObject"
    )
    mock_minio._s3.upload_fileobj = MagicMock()
    with patch("api.minio_client.get_client", return_value=mock_minio), \
         patch("api.routes.clip._generate_clip_bytes",
               AsyncMock(return_value=b"fake-wav")):
        resp = client.get("/jobs/job1/segment/0/audio")
    assert resp.status_code == 200
    assert mock_minio._s3.upload_fileobj.called

def test_get_segment_audio_invalid_index(client):
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    with patch("api.minio_client.get_client", return_value=mock_minio):
        resp = client.get("/jobs/job1/segment/99/audio")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_clip_route.py -v
```

Expected: FAIL — 404 (route not registered)

- [ ] **Step 3: Write api/routes/clip.py**

```python
# api/routes/clip.py
import asyncio
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request

from api import minio_client as minio_mod

router = APIRouter(tags=["clip"])

CLIP_TTL_SEC = 600  # 10 minutes presigned URL expiry


async def _generate_clip_bytes(
    minio_client,
    job_id: str,
    stem: str,
    start: float,
    end: float,
) -> bytes:
    """Download clean.wav from MinIO, ffmpeg-clip the segment, return bytes. Runs in executor."""
    def _sync() -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"{stem}_clean.wav"
            minio_client._s3.download_file(
                minio_client.output_bucket,
                f"output/{job_id}/{stem}_clean.wav",
                str(src),
            )
            out = Path(tmp) / "clip.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src),
                 "-ss", str(start), "-to", str(end),
                 "-c", "copy", str(out)],
                check=True, capture_output=True,
            )
            return out.read_bytes()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync)


@router.get("/jobs/{job_id}/segment/{index}/audio")
async def get_segment_audio(job_id: str, index: int, request: Request):
    minio = minio_mod.get_client()

    # Load segments list to validate index + get timestamps
    try:
        obj = minio._s3.get_object(
            Bucket=minio.output_bucket,
            Key=f"output/{job_id}/",   # prefix — find segments.json
        )
    except ClientError:
        pass  # will try exact key below

    # Try exact key pattern: output/{job_id}/*_segments.json via listing
    # For simplicity, we need the stem — get it from the job row
    pool = request.app.state.pool
    from api import db
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    stem = job["filename"].rsplit(".", 1)[0]
    segments_key = f"output/{job_id}/{stem}_segments.json"

    try:
        obj = minio._s3.get_object(Bucket=minio.output_bucket, Key=segments_key)
        segments: list[dict] = json.loads(obj["Body"].read())
    except ClientError:
        raise HTTPException(status_code=404, detail="Segments not found — job may not be complete")

    if index < 0 or index >= len(segments):
        raise HTTPException(status_code=404,
                            detail=f"Segment index {index} out of range (0–{len(segments)-1})")

    seg   = segments[index]
    start = seg["start"]
    end   = seg["end"]

    clip_key = f"clips/{job_id}/{index}.wav"
    clips_bucket = getattr(minio, "clips_bucket",
                           os.getenv("MINIO_CLIPS_BUCKET", "mediaflow-clips"))

    # Check cache
    try:
        minio._s3.head_object(Bucket=clips_bucket, Key=clip_key)
        # Cache hit
    except ClientError:
        # Cache miss — generate clip
        clip_bytes = await _generate_clip_bytes(minio, job_id, stem, start, end)
        minio._s3.upload_fileobj(
            io.BytesIO(clip_bytes),
            clips_bucket,
            clip_key,
            ExtraArgs={"ContentType": "audio/wav"},
        )

    url = minio.presign_get_url(clips_bucket, clip_key, expires_in=CLIP_TTL_SEC)
    return {"url": url, "expires_in": CLIP_TTL_SEC, "start": start, "end": end}
```

- [ ] **Step 4: Register router in api/main.py**

```python
from api.routes import clip
app.include_router(clip.router)
```

- [ ] **Step 5: Run all clip tests**

```bash
pytest tests/test_clip_route.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/clip.py api/main.py tests/test_clip_route.py
git commit -m "feat(api): GET /jobs/{id}/segment/{index}/audio — on-demand clip with MinIO clips/ cache"
```

---

## Self-Review Checklist

- [ ] `apply_correction` with index not in `edit_map` leaves that segment's text unchanged
- [ ] `apply_correction` called when `verification_status = "in_progress"` does NOT change status (stays `in_progress`)
- [ ] `finalize_verification` sets `verified_at` to current time (not None)
- [ ] `POST /jobs/{id}/correction/finalize` from `verification_status = "unverified"` (no prior edits) is valid — sets `verified` directly
- [ ] Segment clip route returns 404 for `index >= len(segments)` — not a 500
- [ ] `_generate_clip_bytes` runs in `run_in_executor` — never blocks the event loop
- [ ] Cached clip (head_object succeeds) skips ffmpeg entirely
- [ ] `clips_bucket` lifecycle TTL is set in `api/minio_client.py` ensure_buckets (from Sub-plan D Task 3)
