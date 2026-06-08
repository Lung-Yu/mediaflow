# Upload + Batch Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add web/API file upload (up to 5 GB) via MinIO presigned multipart URLs and a queue consumer that feeds the existing pipeline watcher one file at a time.

**Architecture:** Browser calls `POST /upload/init` → gets presigned part URLs → uploads chunks directly to MinIO → calls `POST /upload/complete` → task enters SQLite queue. An asyncio loop in the API polls every 5 s; when a pipeline slot is free it downloads the file from MinIO to `workspace/1_input/` so the existing watcher picks it up. On completion, outputs are backed up to MinIO.

**Tech Stack:** boto3 (S3-compatible MinIO client), FastAPI TestClient + unittest.mock for tests, pytest-asyncio for async test, vanilla JS fetch for browser multipart upload, HTMX for queue panel polling.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `api/minio_client.py` | boto3 wrapper: buckets, presigned URLs, download, upload |
| Create | `api/routes/upload.py` | POST /upload/init, /upload/complete, GET /upload/queue, DELETE /upload/queue/{stem} |
| Create | `api/mq/queue_consumer.py` | asyncio loop: pending → download → workspace |
| Create | `web/templates/upload.html` | drag-and-drop upload page with JS multipart logic |
| Create | `web/templates/partials/queue.html` | HTMX queue status partial |
| Create | `tests/test_minio_client.py` | unit tests for MinIO wrapper |
| Create | `tests/test_upload_routes.py` | unit tests for upload API routes |
| Create | `tests/test_queue_consumer.py` | unit tests for queue consumer loop |
| Modify | `docker-compose.yml` | add minio service + volume |
| Modify | `config.yaml.example` | add minio + upload sections |
| Modify | `requirements.txt` | add boto3, pytest-asyncio |
| Modify | `api/db.py` | schema migration + 5 new query helpers |
| Modify | `api/event_processor.py` | upload outputs to MinIO on task.completed |
| Modify | `api/main.py` | init MinIO client + start queue consumer in lifespan |
| Modify | `web/main.py` | add /upload page, /upload/init proxy, /upload/complete proxy, /partial/queue |
| Modify | `web/templates/dashboard.html` | add queue panel with HTMX poll |
| Modify | `web/templates/base.html` | add Upload nav link |

---

## Task 1: Infrastructure — MinIO Docker + Config + Dependencies

**Files:**
- Modify: `docker-compose.yml`
- Modify: `config.yaml.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Add MinIO service to docker-compose.yml**

Open `docker-compose.yml` and add the `minio` service after `redis`, and add `minio-data` to the volumes block:

```yaml
  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    restart: unless-stopped
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY:-mediaflow}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY:-changeme}
    volumes:
      - minio-data:/data
    ports:
      - "9000:9000"
      - "9001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5
```

In the `api` service, add MinIO env vars:

```yaml
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - DB_PATH=/app/data/pipeline.db
      - WORKSPACE_DIR=/workspace
      - WEBHOOK_URL=${WEBHOOK_URL:-}
      - MINIO_ENDPOINT=${MINIO_ENDPOINT:-minio:9000}
      - MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-mediaflow}
      - MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-changeme}
      - MINIO_SECURE=${MINIO_SECURE:-false}
      - MINIO_INPUT_BUCKET=${MINIO_INPUT_BUCKET:-mediaflow-input}
      - MINIO_OUTPUT_BUCKET=${MINIO_OUTPUT_BUCKET:-mediaflow-output}
      - UPLOAD_MAX_FILE_BYTES=${UPLOAD_MAX_FILE_BYTES:-5368709120}
      - UPLOAD_PART_SIZE_BYTES=${UPLOAD_PART_SIZE_BYTES:-104857600}
      - UPLOAD_MAX_CONCURRENT=${UPLOAD_MAX_CONCURRENT:-2}
```

Add `depends_on` for minio in the api service:

```yaml
    depends_on:
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
```

Add to `volumes:` block at bottom:

```yaml
  minio-data:
```

- [ ] **Step 2: Add config.yaml.example sections**

Add to the end of `config.yaml.example`:

```yaml
minio:
  endpoint: localhost:9000
  access_key: mediaflow
  secret_key: changeme
  secure: false
  input_bucket: mediaflow-input
  output_bucket: mediaflow-output

upload:
  max_file_bytes: 5368709120   # 5 GB
  part_size_bytes: 104857600   # 100 MB per part
  max_concurrent: 2
```

- [ ] **Step 3: Add dependencies to requirements.txt**

```
boto3==1.35.99
pytest-asyncio==0.24.0
```

Add `boto3` under the `# API` section. Add `pytest-asyncio` under `# Dev`.

- [ ] **Step 4: Install and smoke-test**

```bash
source venv/bin/activate
pip install boto3==1.35.99 pytest-asyncio==0.24.0
python -c "import boto3; print('boto3 ok')"
```

Expected: `boto3 ok`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml config.yaml.example requirements.txt
git commit -m "feat(infra): add MinIO to docker-compose, upload config, boto3 dep"
```

---

## Task 2: DB Schema Migration + New Query Helpers

**Files:**
- Modify: `api/db.py`
- Create: `tests/test_db_upload.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_db_upload.py`:

```python
"""Tests for upload-related DB helpers."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())
    return db_mod


def test_schema_has_minio_columns(tmp_db):
    import aiosqlite

    async def check():
        async with aiosqlite.connect(tmp_db.DB_PATH) as db:
            cur = await db.execute("PRAGMA table_info(tasks)")
            cols = {row[1] for row in await cur.fetchall()}
        assert "minio_input_key" in cols
        assert "minio_output_prefix" in cols

    asyncio.get_event_loop().run_until_complete(check())


def test_get_task_returns_none_for_missing(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(tmp_db.get_task("nonexistent"))
    assert result is None


def test_get_task_returns_dict_after_upsert(tmp_db):
    async def run():
        await tmp_db.upsert_task("s1", filename="s1.mp4", status="pending",
                                  minio_input_key="s1/s1.mp4")
        return await tmp_db.get_task("s1")

    task = asyncio.get_event_loop().run_until_complete(run())
    assert task["status"] == "pending"
    assert task["minio_input_key"] == "s1/s1.mp4"


def test_count_active_tasks(tmp_db):
    async def run():
        for stem, status in [("a", "downloading"), ("b", "processing"),
                               ("c", "completed"), ("d", "pending")]:
            await tmp_db.upsert_task(stem, status=status)
        return await tmp_db.count_active_tasks()

    count = asyncio.get_event_loop().run_until_complete(run())
    assert count == 2  # downloading + processing (completed and pending excluded)


def test_get_oldest_pending(tmp_db):
    import time

    async def run():
        t1 = time.time() - 10
        t2 = time.time()
        await tmp_db.upsert_task("old", status="pending", submitted_at=t1,
                                  minio_input_key="old/f.mp4", filename="f.mp4")
        await tmp_db.upsert_task("new", status="pending", submitted_at=t2,
                                  minio_input_key="new/f.mp4", filename="f.mp4")
        return await tmp_db.get_oldest_pending()

    task = asyncio.get_event_loop().run_until_complete(run())
    assert task["stem"] == "old"


def test_get_oldest_pending_returns_none_when_empty(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(tmp_db.get_oldest_pending())
    assert result is None


def test_get_upload_queue_filters_by_minio_key(tmp_db):
    async def run():
        await tmp_db.upsert_task("with_minio", status="pending",
                                  minio_input_key="with_minio/f.mp4", filename="f.mp4")
        await tmp_db.upsert_task("no_minio", status="completed", filename="g.mp4")
        return await tmp_db.get_upload_queue()

    tasks = asyncio.get_event_loop().run_until_complete(run())
    stems = [t["stem"] for t in tasks]
    assert "with_minio" in stems
    assert "no_minio" not in stems


def test_delete_task(tmp_db):
    async def run():
        await tmp_db.upsert_task("to_delete", status="pending",
                                  minio_input_key="x/f.mp4", filename="f.mp4")
        await tmp_db.delete_task("to_delete")
        return await tmp_db.get_task("to_delete")

    result = asyncio.get_event_loop().run_until_complete(run())
    assert result is None
```

- [ ] **Step 2: Run tests — expect failures**

```bash
source venv/bin/activate
pytest tests/test_db_upload.py -v 2>&1 | head -30
```

Expected: multiple errors (functions not defined yet).

- [ ] **Step 3: Add schema migration and helpers to api/db.py**

In `api/db.py`, replace the `init()` function and add new helpers:

```python
_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN minio_input_key TEXT",
    "ALTER TABLE tasks ADD COLUMN minio_output_prefix TEXT",
]


async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        for sql in _MIGRATIONS:
            try:
                await db.execute(sql)
            except Exception:
                pass  # column already exists
        await db.commit()


async def get_task(stem: str) -> "dict | None":
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE stem = ?", (stem,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def count_active_tasks() -> int:
    """Count tasks occupying a pipeline slot."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM tasks "
            "WHERE status IN ('downloading','queued','submitted','processing')"
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def get_oldest_pending() -> "dict | None":
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'pending' "
            "ORDER BY submitted_at ASC LIMIT 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_upload_queue() -> list:
    """All upload-originated tasks (minio_input_key IS NOT NULL), newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE minio_input_key IS NOT NULL "
            "ORDER BY submitted_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def delete_task(stem: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE stem = ?", (stem,))
        await db.commit()
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_db_upload.py -v
```

Expected: 8 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/db.py tests/test_db_upload.py
git commit -m "feat(db): minio columns migration + upload queue helpers"
```

---

## Task 3: MinIO Client Wrapper

**Files:**
- Create: `api/minio_client.py`
- Create: `tests/test_minio_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_minio_client.py`:

```python
"""Unit tests for MinIO client wrapper."""
import math
from unittest.mock import MagicMock, patch, call
from pathlib import Path
import pytest


@pytest.fixture
def mock_boto3():
    with patch("api.minio_client.boto3") as mock:
        mock_s3 = MagicMock()
        mock.client.return_value = mock_s3
        yield mock, mock_s3


@pytest.fixture
def client(mock_boto3):
    import importlib
    import api.minio_client as mod
    importlib.reload(mod)
    return mod.MinIOClient(
        endpoint="localhost:9000",
        access_key="testkey",
        secret_key="testsecret",
        secure=False,
        input_bucket="test-input",
        output_bucket="test-output",
    )


def test_ensure_buckets_creates_both(client, mock_boto3):
    _, s3 = mock_boto3
    s3.create_bucket.side_effect = [None, None]
    client.ensure_buckets()
    assert s3.create_bucket.call_count == 2
    calls = [c[1]["Bucket"] for c in s3.create_bucket.call_args_list]
    assert "test-input" in calls
    assert "test-output" in calls


def test_ensure_buckets_sets_cors(client, mock_boto3):
    _, s3 = mock_boto3
    client.ensure_buckets()
    s3.put_bucket_cors.assert_called_once()
    args = s3.put_bucket_cors.call_args[1]
    assert args["Bucket"] == "test-input"
    rules = args["CORSConfiguration"]["CORSRules"]
    assert any("ETag" in r.get("ExposeHeaders", []) for r in rules)


def test_ensure_buckets_ignores_already_owned(client, mock_boto3):
    _, s3 = mock_boto3
    s3.create_bucket.side_effect = Exception("BucketAlreadyOwnedByYou")
    client.ensure_buckets()  # should not raise


def test_create_multipart_upload_returns_upload_id(client, mock_boto3):
    _, s3 = mock_boto3
    s3.create_multipart_upload.return_value = {"UploadId": "uid-123"}
    result = client.create_multipart_upload("stem/file.mp4")
    assert result == "uid-123"
    s3.create_multipart_upload.assert_called_with(Bucket="test-input", Key="stem/file.mp4")


def test_presign_part_urls_returns_correct_count(client, mock_boto3):
    _, s3 = mock_boto3
    s3.generate_presigned_url.return_value = "http://minio/presigned"
    parts = client.presign_part_urls("stem/f.mp4", "uid-1", 3)
    assert len(parts) == 3
    assert parts[0]["part_number"] == 1
    assert parts[2]["part_number"] == 3
    assert all("url" in p for p in parts)


def test_complete_multipart_upload(client, mock_boto3):
    _, s3 = mock_boto3
    parts = [{"part_number": 1, "etag": '"abc"'}, {"part_number": 2, "etag": '"def"'}]
    client.complete_multipart_upload("stem/f.mp4", "uid-1", parts)
    s3.complete_multipart_upload.assert_called_once()
    call_kwargs = s3.complete_multipart_upload.call_args[1]
    assert call_kwargs["Bucket"] == "test-input"
    assert call_kwargs["Key"] == "stem/f.mp4"
    assert call_kwargs["UploadId"] == "uid-1"
    assert len(call_kwargs["MultipartUpload"]["Parts"]) == 2


def test_abort_multipart_upload(client, mock_boto3):
    _, s3 = mock_boto3
    client.abort_multipart_upload("stem/f.mp4", "uid-1")
    s3.abort_multipart_upload.assert_called_with(
        Bucket="test-input", Key="stem/f.mp4", UploadId="uid-1"
    )


def test_download_to_file(client, mock_boto3, tmp_path):
    _, s3 = mock_boto3
    dest = tmp_path / "out.mp4"
    client.download_to_file("stem/f.mp4", dest)
    s3.download_file.assert_called_with("test-input", "stem/f.mp4", str(dest))


def test_upload_outputs_uploads_existing_files(client, mock_boto3, tmp_path):
    _, s3 = mock_boto3
    (tmp_path / "stem.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    (tmp_path / "stem_summary.md").write_text("# Summary")
    client.upload_outputs("stem", tmp_path)
    uploaded_keys = [c[1]["Key"] for c in s3.upload_file.call_args_list]
    assert "stem/stem.srt" in uploaded_keys
    assert "stem/stem_summary.md" in uploaded_keys


def test_upload_outputs_skips_missing_files(client, mock_boto3, tmp_path):
    _, s3 = mock_boto3
    client.upload_outputs("stem", tmp_path)  # no files exist
    s3.upload_file.assert_not_called()


def test_presign_get_url(client, mock_boto3):
    _, s3 = mock_boto3
    s3.generate_presigned_url.return_value = "http://minio/download"
    url = client.presign_get_url("test-output", "stem/stem.srt")
    assert url == "http://minio/download"
    s3.generate_presigned_url.assert_called_with(
        "get_object",
        Params={"Bucket": "test-output", "Key": "stem/stem.srt"},
        ExpiresIn=604800,
    )
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_minio_client.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'api.minio_client'`

- [ ] **Step 3: Implement api/minio_client.py**

Create `api/minio_client.py`:

```python
"""boto3 wrapper for MinIO operations used by the upload flow."""
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "mediaflow")
SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "changeme")
SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
INPUT_BUCKET = os.getenv("MINIO_INPUT_BUCKET", "mediaflow-input")
OUTPUT_BUCKET = os.getenv("MINIO_OUTPUT_BUCKET", "mediaflow-output")

_OUTPUT_STEMS = [".srt", "_summary.md", "_summary.json", "_chapters.json"]


class MinIOClient:
    def __init__(
        self, endpoint: str, access_key: str, secret_key: str,
        secure: bool, input_bucket: str, output_bucket: str,
    ):
        scheme = "https" if secure else "http"
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )
        self.input_bucket = input_bucket
        self.output_bucket = output_bucket

    def ensure_buckets(self) -> None:
        """Create buckets if absent; set CORS on input bucket for browser direct upload."""
        for bucket in [self.input_bucket, self.output_bucket]:
            try:
                self._s3.create_bucket(Bucket=bucket)
            except Exception:
                pass  # already exists
        self._s3.put_bucket_cors(
            Bucket=self.input_bucket,
            CORSConfiguration={
                "CORSRules": [{
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedOrigins": ["*"],
                    "ExposeHeaders": ["ETag"],
                }]
            },
        )

    def create_multipart_upload(self, key: str) -> str:
        resp = self._s3.create_multipart_upload(Bucket=self.input_bucket, Key=key)
        return resp["UploadId"]

    def presign_part_urls(self, key: str, upload_id: str, num_parts: int) -> list:
        return [
            {
                "part_number": i,
                "url": self._s3.generate_presigned_url(
                    "upload_part",
                    Params={"Bucket": self.input_bucket, "Key": key,
                            "UploadId": upload_id, "PartNumber": i},
                    ExpiresIn=7200,
                ),
            }
            for i in range(1, num_parts + 1)
        ]

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list) -> None:
        self._s3.complete_multipart_upload(
            Bucket=self.input_bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": p["part_number"], "ETag": p["etag"]} for p in parts
                ]
            },
        )

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self._s3.abort_multipart_upload(
            Bucket=self.input_bucket, Key=key, UploadId=upload_id
        )

    def download_to_file(self, key: str, dest: Path) -> None:
        """Blocking — call via run_in_executor from async context."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.input_bucket, key, str(dest))

    def upload_outputs(self, stem: str, output_dir: Path) -> None:
        """Upload SRT and summary files for stem to the output bucket."""
        for suffix in _OUTPUT_STEMS:
            path = output_dir / f"{stem}{suffix}"
            if path.exists():
                self._s3.upload_file(
                    str(path), self.output_bucket, f"{stem}/{stem}{suffix}"
                )

    def presign_get_url(self, bucket: str, key: str, expires_in: int = 604800) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )


_client: Optional[MinIOClient] = None


def get_client() -> MinIOClient:
    assert _client is not None, "MinIO client not initialized — call init_client() first"
    return _client


def init_client() -> MinIOClient:
    global _client
    _client = MinIOClient(
        endpoint=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=SECURE,
        input_bucket=INPUT_BUCKET,
        output_bucket=OUTPUT_BUCKET,
    )
    return _client
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_minio_client.py -v
```

Expected: 12 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/minio_client.py tests/test_minio_client.py
git commit -m "feat(api): MinIO boto3 client wrapper with presigned multipart support"
```

---

## Task 4: Upload API Routes — Init + Complete

**Files:**
- Create: `api/routes/upload.py` (init + complete endpoints only)
- Create: `tests/test_upload_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_upload_routes.py`:

```python
"""Tests for upload API routes."""
import asyncio
import math
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())


@pytest.fixture
def mock_minio():
    m = MagicMock()
    m.create_multipart_upload.return_value = "test-upload-id"
    m.presign_part_urls.return_value = [
        {"part_number": 1, "url": "http://minio/part1"},
        {"part_number": 2, "url": "http://minio/part2"},
    ]
    with patch("api.minio_client.get_client", return_value=m):
        yield m


@pytest.fixture
def client(mock_minio):
    from fastapi import FastAPI
    from api.routes import upload as upload_mod
    import importlib
    importlib.reload(upload_mod)
    app = FastAPI()
    app.include_router(upload_mod.router)
    return TestClient(app)


def test_upload_init_returns_presigned_parts(client, mock_minio):
    resp = client.post("/upload/init", json={
        "filename": "lecture.mp4",
        "size_bytes": 200 * 1024 * 1024,  # 200 MB → 2 parts
        "content_type": "video/mp4",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["upload_id"] == "test-upload-id"
    assert data["stem"] == "lecture"
    assert data["minio_key"] == "lecture/lecture.mp4"
    assert data["part_size"] == 100 * 1024 * 1024
    assert len(data["parts"]) == 2


def test_upload_init_rejects_oversized_file(client, mock_minio, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_FILE_BYTES", str(100 * 1024 * 1024))
    resp = client.post("/upload/init", json={
        "filename": "huge.mp4",
        "size_bytes": 200 * 1024 * 1024,
    })
    assert resp.status_code == 400


def test_upload_init_stem_sanitisation(client, mock_minio):
    resp = client.post("/upload/init", json={
        "filename": "My Lecture 01.mp4",
        "size_bytes": 50 * 1024 * 1024,
    })
    assert resp.status_code == 200
    assert resp.json()["stem"] == "my_lecture_01"


def test_upload_init_rejects_duplicate_pending(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("lecture", status="processing", filename="lecture.mp4")
    )
    resp = client.post("/upload/init", json={
        "filename": "lecture.mp4",
        "size_bytes": 50 * 1024 * 1024,
    })
    assert resp.status_code == 409


def test_upload_init_allows_retry_after_failure(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("lecture", status="failed", filename="lecture.mp4")
    )
    resp = client.post("/upload/init", json={
        "filename": "lecture.mp4",
        "size_bytes": 50 * 1024 * 1024,
    })
    assert resp.status_code == 200


def test_upload_complete_creates_pending_task(client, mock_minio):
    resp = client.post("/upload/complete", json={
        "upload_id": "test-upload-id",
        "minio_key": "lecture/lecture.mp4",
        "parts": [
            {"part_number": 1, "etag": '"abc"'},
            {"part_number": 2, "etag": '"def"'},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["stem"] == "lecture"
    assert data["status"] == "pending"
    mock_minio.complete_multipart_upload.assert_called_once()


def test_upload_complete_stores_minio_key(client, mock_minio):
    import api.db as db_mod
    client.post("/upload/complete", json={
        "upload_id": "uid",
        "minio_key": "lecture/lecture.mp4",
        "parts": [{"part_number": 1, "etag": '"abc"'}],
    })
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("lecture"))
    assert task["minio_input_key"] == "lecture/lecture.mp4"
    assert task["status"] == "pending"
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_upload_routes.py -v 2>&1 | head -20
```

Expected: import errors (module not yet created).

- [ ] **Step 3: Implement init + complete endpoints in api/routes/upload.py**

Create `api/routes/upload.py`:

```python
"""Upload API routes — multipart presigned URL flow."""
import math
import os
import re
import time
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import db
from api import minio_client as minio_mod

router = APIRouter(prefix="/upload")

MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(5 * 1024 ** 3)))
PART_SIZE = int(os.getenv("UPLOAD_PART_SIZE_BYTES", str(100 * 1024 * 1024)))


class InitRequest(BaseModel):
    filename: str
    size_bytes: int
    content_type: str = "application/octet-stream"


class PartInfo(BaseModel):
    part_number: int
    etag: str


class CompleteRequest(BaseModel):
    upload_id: str
    minio_key: str
    parts: List[PartInfo]


def _stem_from_filename(filename: str) -> str:
    name = filename.rsplit(".", 1)[0]
    name = re.sub(r"[^\w\-]", "_", name.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "upload"


@router.post("/init")
async def upload_init(req: InitRequest):
    if req.size_bytes > MAX_FILE_BYTES:
        raise HTTPException(400, f"File too large ({req.size_bytes} bytes). Max: {MAX_FILE_BYTES}")

    stem = _stem_from_filename(req.filename)
    existing = await db.get_task(stem)
    if existing and existing["status"] not in ("completed", "failed"):
        raise HTTPException(409, f"Task '{stem}' already active (status={existing['status']})")

    minio_key = f"{stem}/{req.filename}"
    client = minio_mod.get_client()
    upload_id = client.create_multipart_upload(minio_key)
    num_parts = math.ceil(req.size_bytes / PART_SIZE)
    parts = client.presign_part_urls(minio_key, upload_id, num_parts)

    return {
        "upload_id": upload_id,
        "minio_key": minio_key,
        "stem": stem,
        "part_size": PART_SIZE,
        "parts": parts,
    }


@router.post("/complete")
async def upload_complete(req: CompleteRequest):
    stem = req.minio_key.split("/")[0]
    filename = req.minio_key.split("/", 1)[1]
    client = minio_mod.get_client()
    client.complete_multipart_upload(
        req.minio_key,
        req.upload_id,
        [{"part_number": p.part_number, "etag": p.etag} for p in req.parts],
    )
    await db.upsert_task(
        stem,
        filename=filename,
        status="pending",
        minio_input_key=req.minio_key,
        submitted_at=time.time(),
    )
    return {"stem": stem, "status": "pending"}
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_upload_routes.py -v
```

Expected: 8 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/routes/upload.py tests/test_upload_routes.py
git commit -m "feat(api): upload init + complete endpoints with presigned multipart"
```

---

## Task 5: Upload API Routes — Queue + Cancel

**Files:**
- Modify: `api/routes/upload.py` (add queue + cancel endpoints)
- Modify: `tests/test_upload_routes.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_upload_routes.py`:

```python
def test_get_queue_returns_upload_tasks(client, mock_minio):
    import api.db as db_mod
    mock_minio.presign_get_url.return_value = "http://minio/dl"
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s1", status="pending", minio_input_key="s1/f.mp4",
                            filename="f.mp4", submitted_at=1000.0)
    )
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s2", status="completed", filename="g.mp4")  # no minio key
    )
    resp = client.get("/upload/queue")
    assert resp.status_code == 200
    stems = [t["stem"] for t in resp.json()]
    assert "s1" in stems
    assert "s2" not in stems


def test_get_queue_includes_download_urls_for_completed(client, mock_minio):
    import api.db as db_mod
    mock_minio.presign_get_url.return_value = "http://minio/download.srt"
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("done_stem", status="completed",
                            minio_input_key="done_stem/f.mp4", filename="f.mp4",
                            minio_output_prefix="done_stem/")
    )
    resp = client.get("/upload/queue")
    task = next(t for t in resp.json() if t["stem"] == "done_stem")
    assert ".srt" in task["downloads"]


def test_delete_queue_cancels_pending(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("to_cancel", status="pending",
                            minio_input_key="to_cancel/f.mp4", filename="f.mp4")
    )
    resp = client.delete("/upload/queue/to_cancel")
    assert resp.status_code == 200
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("to_cancel"))
    assert task is None


def test_delete_queue_rejects_active_task(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("active", status="processing",
                            minio_input_key="active/f.mp4", filename="f.mp4")
    )
    resp = client.delete("/upload/queue/active")
    assert resp.status_code == 409


def test_delete_queue_404_for_missing(client, mock_minio):
    resp = client.delete("/upload/queue/ghost")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run new tests — expect failures**

```bash
pytest tests/test_upload_routes.py::test_get_queue_returns_upload_tasks -v
```

Expected: FAILED (endpoints not yet defined).

- [ ] **Step 3: Add queue + cancel endpoints to api/routes/upload.py**

Append to the bottom of `api/routes/upload.py`:

```python
@router.get("/queue")
async def get_queue():
    tasks = await db.get_upload_queue()
    client = minio_mod.get_client()
    result = []
    for t in tasks:
        entry = {
            "stem": t["stem"],
            "filename": t["filename"],
            "status": t["status"],
            "queued_at": t.get("submitted_at"),
            "current_stage": t.get("current_stage"),
            "error_msg": t.get("error_msg"),
            "downloads": {},
        }
        if t["status"] == "completed" and t.get("minio_output_prefix"):
            prefix = t["minio_output_prefix"]
            output_bucket = minio_mod.OUTPUT_BUCKET
            for suffix in [".srt", "_summary.md", "_summary.json"]:
                key = f"{prefix}{t['stem']}{suffix}"
                try:
                    entry["downloads"][suffix] = client.presign_get_url(output_bucket, key)
                except Exception:
                    pass
        result.append(entry)
    return result


@router.delete("/queue/{stem}")
async def cancel_upload(stem: str):
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(404, f"Task '{stem}' not found")
    if task["status"] != "pending":
        raise HTTPException(409, f"Cannot cancel task with status '{task['status']}'")
    await db.delete_task(stem)
    return {"cancelled": stem}
```

- [ ] **Step 4: Run all upload route tests — expect all pass**

```bash
pytest tests/test_upload_routes.py -v
```

Expected: 13 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/routes/upload.py tests/test_upload_routes.py
git commit -m "feat(api): upload queue list + cancel endpoints"
```

---

## Task 6: Queue Consumer

**Files:**
- Create: `api/mq/queue_consumer.py`
- Create: `tests/test_queue_consumer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_queue_consumer.py`:

```python
"""Tests for queue consumer asyncio loop."""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_db():
    with patch("api.mq.queue_consumer.db") as m:
        m.count_active_tasks = AsyncMock(return_value=0)
        m.get_oldest_pending = AsyncMock(return_value=None)
        m.upsert_task = AsyncMock()
        yield m


@pytest.fixture
def mock_minio():
    with patch("api.mq.queue_consumer.minio_mod") as m:
        client = MagicMock()
        client.download_to_file = MagicMock()
        m.get_client.return_value = client
        yield m, client


async def test_tick_does_nothing_when_at_capacity(mock_db, mock_minio):
    mock_db.count_active_tasks.return_value = 2
    from api.mq import queue_consumer
    await queue_consumer._tick()
    mock_db.get_oldest_pending.assert_not_called()


async def test_tick_does_nothing_when_no_pending(mock_db, mock_minio):
    mock_db.get_oldest_pending.return_value = None
    from api.mq import queue_consumer
    await queue_consumer._tick()
    mock_db.upsert_task.assert_not_called()


async def test_tick_downloads_pending_task(mock_db, mock_minio, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    mock_db.get_oldest_pending.return_value = {
        "stem": "lecture",
        "filename": "lecture.mp4",
        "minio_input_key": "lecture/lecture.mp4",
    }
    import importlib
    import api.mq.queue_consumer as mod
    importlib.reload(mod)

    with patch.object(mod, "db", mock_db), \
         patch.object(mod, "minio_mod", mock_minio[0]):
        await mod._tick()

    status_calls = [c[1]["status"] for c in mock_db.upsert_task.call_args_list
                    if "status" in c[1]]
    assert "downloading" in status_calls
    assert "queued" in status_calls
    mock_minio[1].download_to_file.assert_called_once()


async def test_tick_sets_failed_on_download_error(mock_db, mock_minio, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    mock_db.get_oldest_pending.return_value = {
        "stem": "broken",
        "filename": "broken.mp4",
        "minio_input_key": "broken/broken.mp4",
    }
    mock_minio[1].download_to_file.side_effect = Exception("MinIO unreachable")

    import importlib
    import api.mq.queue_consumer as mod
    importlib.reload(mod)

    with patch.object(mod, "db", mock_db), \
         patch.object(mod, "minio_mod", mock_minio[0]):
        await mod._tick()

    failed_call = next(
        (c for c in mock_db.upsert_task.call_args_list if c[1].get("status") == "failed"),
        None
    )
    assert failed_call is not None
    assert "MinIO unreachable" in failed_call[1]["error_msg"]
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_queue_consumer.py -v 2>&1 | head -20
```

Expected: import errors.

- [ ] **Step 3: Implement api/mq/queue_consumer.py**

Create `api/mq/queue_consumer.py`:

```python
"""Queue consumer — polls pending upload tasks and downloads them to workspace.

Runs as an asyncio background task inside the API lifespan.
Every 5 s, checks if a pipeline slot is free and downloads the
oldest pending task from MinIO to workspace/1_input/ for the watcher.
"""
import asyncio
import logging
import os
from pathlib import Path

from api import db
from api import minio_client as minio_mod

log = logging.getLogger(__name__)

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
MAX_CONCURRENT = int(os.getenv("UPLOAD_MAX_CONCURRENT", "2"))


async def _tick() -> None:
    active = await db.count_active_tasks()
    if active >= MAX_CONCURRENT:
        return
    task = await db.get_oldest_pending()
    if not task:
        return

    stem = task["stem"]
    filename = task["filename"]
    minio_key = task["minio_input_key"]
    dest = WORKSPACE / "1_input" / filename

    log.info("Queue: slot free (%d/%d active), downloading %s", active, MAX_CONCURRENT, stem)
    await db.upsert_task(stem, status="downloading")
    try:
        client = minio_mod.get_client()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, client.download_to_file, minio_key, dest)
        await db.upsert_task(stem, status="queued")
        log.info("Queue: %s ready in workspace, watcher will pick up", stem)
    except Exception as exc:
        log.error("Queue: download failed for %s: %s", stem, exc)
        await db.upsert_task(stem, status="failed", error_msg=str(exc))


async def run() -> None:
    log.info("Queue consumer started (max_concurrent=%d)", MAX_CONCURRENT)
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            log.info("Queue consumer stopping")
            return
        except Exception as exc:
            log.error("Queue consumer error: %s", exc)
        await asyncio.sleep(5)
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_queue_consumer.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/mq/queue_consumer.py tests/test_queue_consumer.py
git commit -m "feat(api): queue consumer asyncio loop — pending → download → workspace"
```

---

## Task 7: Event Processor — Output Backup on task.completed

**Files:**
- Modify: `api/event_processor.py`
- Modify: `tests/` (add test for backup behaviour)

- [ ] **Step 1: Write failing test**

Create `tests/test_event_processor_minio.py`:

```python
"""Test that task.completed triggers MinIO output backup."""
import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())


@pytest.fixture
def mock_minio_client():
    m = MagicMock()
    m.upload_outputs = MagicMock()
    return m


def test_task_completed_triggers_minio_backup(mock_minio_client, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "3_output").mkdir(parents=True)

    with patch("api.minio_client.get_client", return_value=mock_minio_client), \
         patch("api.minio_client._client", mock_minio_client):

        import importlib
        import api.event_processor as ep
        importlib.reload(ep)

        asyncio.get_event_loop().run_until_complete(ep.process_event({
            "event": "task.completed",
            "stem": "lecture",
            "output_path": str(tmp_path / "3_output" / "lecture.srt"),
            "ts": str(time.time()),
        }))

    mock_minio_client.upload_outputs.assert_called_once()
    call_args = mock_minio_client.upload_outputs.call_args
    assert call_args[0][0] == "lecture"


def test_task_completed_backup_failure_does_not_raise(mock_minio_client, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "3_output").mkdir(parents=True)
    mock_minio_client.upload_outputs.side_effect = Exception("MinIO down")

    with patch("api.minio_client.get_client", return_value=mock_minio_client), \
         patch("api.minio_client._client", mock_minio_client):

        import importlib
        import api.event_processor as ep
        importlib.reload(ep)

        # Should not raise — backup failure is non-fatal
        asyncio.get_event_loop().run_until_complete(ep.process_event({
            "event": "task.completed",
            "stem": "lecture",
            "ts": str(time.time()),
        }))
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_event_processor_minio.py -v 2>&1 | head -20
```

Expected: `AssertionError` — backup not called yet.

- [ ] **Step 3: Update api/event_processor.py**

Add these imports at the top of `api/event_processor.py`:

```python
import asyncio
import logging
import os
from pathlib import Path
```

Add `log = logging.getLogger(__name__)` after the imports.

Replace the `if event in _TERMINAL:` block (the notify call) with:

```python
    if event in _TERMINAL:
        await notify({
            "stem": stem,
            "status": new_status,
            "event": event,
            "output_srt_path": fields.get("output_path", ""),
            "error_msg": fields.get("error_msg", ""),
            "completed_at": ts,
        })

    if event == "task.completed":
        try:
            from api import minio_client as minio_mod
            client = minio_mod.get_client()
            output_dir = Path(os.getenv("WORKSPACE_DIR", "./workspace")) / "3_output"
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, client.upload_outputs, stem, output_dir)
            await db.upsert_task(stem, minio_output_prefix=f"{stem}/")
        except Exception as exc:
            log.warning("MinIO output backup failed for %s: %s", stem, exc)
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_event_processor_minio.py -v
```

Expected: 2 tests PASSED.

- [ ] **Step 5: Confirm existing event processor tests still pass**

```bash
pytest tests/ -v --ignore=tests/run-pipeline-test.sh 2>&1 | tail -10
```

Expected: all tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add api/event_processor.py tests/test_event_processor_minio.py
git commit -m "feat(api): backup pipeline outputs to MinIO on task.completed"
```

---

## Task 8: Wire Up API main.py

**Files:**
- Modify: `api/main.py`
- Modify: `api/routes/__init__.py` (none needed — just import in main)

- [ ] **Step 1: Update api/main.py**

Replace the full contents of `api/main.py`:

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import db
from api import minio_client as minio_mod
from api.reconcile import reconcile
from api.mq import consumer
from api.mq import queue_consumer
from api.routes import events, files, status, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    await reconcile()

    minio_mod.init_client()
    try:
        minio_mod.get_client().ensure_buckets()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("MinIO unavailable on startup: %s", exc)

    redis_task = asyncio.create_task(consumer.run())
    queue_task = asyncio.create_task(queue_consumer.run())
    yield
    for task in [redis_task, queue_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="mediaflow API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(events.router)
app.include_router(files.router)
app.include_router(status.router)
app.include_router(upload.router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 2: Verify API starts without MinIO running (graceful degradation)**

```bash
source venv/bin/activate
python -c "
import asyncio
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
with patch('api.minio_client.MinIOClient.ensure_buckets', side_effect=Exception('no minio')):
    from api.main import app
    client = TestClient(app)
    r = client.get('/health')
    assert r.json() == {'status': 'ok'}
    print('Health check OK with MinIO unavailable')
"
```

Expected: `Health check OK with MinIO unavailable`

- [ ] **Step 3: Commit**

```bash
git add api/main.py
git commit -m "feat(api): wire MinIO init + queue consumer into lifespan"
```

---

## Task 9: Web Upload Page

**Files:**
- Create: `web/templates/upload.html`
- Modify: `web/main.py`

- [ ] **Step 1: Add upload page route and proxy routes to web/main.py**

In `web/main.py`, add imports at top (if not present): `from fastapi import Request`

Add after the existing routes, before `@app.get("/health")`:

```python
# ── Upload page ───────────────────────────────────────────────
@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse(request=request, name="upload.html", context={})


@app.post("/upload/init")
async def upload_init_proxy(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{API_URL}/upload/init", json=body)
        return r.json()


@app.post("/upload/complete")
async def upload_complete_proxy(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{API_URL}/upload/complete", json=body)
        return r.json()
```

- [ ] **Step 2: Add Upload nav link to base.html**

In `web/templates/base.html`, update the nav block. Since `base.html` currently has no nav, the upload and dashboard pages each have their own nav. Instead, add the upload link to `dashboard.html`'s nav:

In `web/templates/dashboard.html`, find:
```html
          <a href="/srts">transcripts</a>
```
And add after it:
```html
          <a href="/upload">upload</a>
```

- [ ] **Step 3: Create web/templates/upload.html**

Create `web/templates/upload.html`:

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>mediaflow — Upload</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="layout">
    <header>
      <div class="wordmark">media<span>flow</span></div>
      <div class="header-meta">
        <nav class="header-nav">
          <a href="/">dashboard</a>
          <a href="/srts">transcripts</a>
          <a href="/upload" class="active">upload</a>
        </nav>
      </div>
    </header>

    <main class="upload-main">
      <div class="card">
        <h2>上傳音訊 / 視訊</h2>

        <div id="drop-zone" class="drop-zone">
          <div class="drop-zone-icon">⬆</div>
          <p>拖曳檔案到這裡，或</p>
          <label class="mock-button">
            選擇檔案
            <input type="file" id="file-input" multiple
                   accept=".mp4,.m4a,.mp3,.wav,.flac" hidden>
          </label>
          <p class="upload-hint">支援 .mp4 .m4a .mp3 .wav .flac｜單檔上限 5 GB</p>
        </div>

        <div id="file-list" class="file-list" hidden></div>

        <button id="start-btn" class="upload-start-btn" hidden>開始上傳</button>
      </div>
    </main>

    <footer>
      <span>mediaflow pipeline monitor</span>
    </footer>
  </div>

  <style>
    .upload-main { padding: 1rem 0; }
    .drop-zone {
      border: 2px dashed #5555aa;
      border-radius: 10px;
      padding: 2.5rem;
      text-align: center;
      background: #f9f9ff;
      cursor: pointer;
      transition: background 0.2s;
    }
    .drop-zone.drag-over { background: #eeeeff; border-color: #3333cc; }
    .drop-zone-icon { font-size: 2rem; margin-bottom: 0.5rem; }
    .upload-hint { color: #888; font-size: 0.8em; margin-top: 0.5rem; }
    .file-list { margin-top: 1rem; }
    .file-row {
      display: flex;
      align-items: center;
      gap: 1rem;
      padding: 0.6rem 0;
      border-bottom: 1px solid #eee;
    }
    .file-row .file-name { flex: 1; font-weight: 500; }
    .file-row .file-size { color: #888; font-size: 0.85em; min-width: 70px; text-align: right; }
    .file-row .file-status { min-width: 120px; font-size: 0.85em; }
    .file-row .progress-bar {
      width: 100px; height: 6px; background: #ddd; border-radius: 3px; overflow: hidden;
    }
    .file-row .progress-fill { height: 100%; background: #5566cc; width: 0%; transition: width 0.3s; }
    .status-waiting { color: #888; }
    .status-uploading { color: #5566cc; }
    .status-done { color: #44aa44; }
    .status-error { color: #cc4444; }
    .upload-start-btn {
      margin-top: 1rem;
      padding: 0.6rem 2rem;
      background: #3344aa;
      color: white;
      border: none;
      border-radius: 6px;
      font-size: 1rem;
      cursor: pointer;
    }
    .upload-start-btn:disabled { background: #999; cursor: default; }
  </style>

  <script>
    const PART_SIZE = 100 * 1024 * 1024; // 100 MB — must match server
    let selectedFiles = [];

    function formatBytes(b) {
      if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
      if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
      return (b / 1e3).toFixed(0) + ' KB';
    }

    function renderFileList() {
      const list = document.getElementById('file-list');
      list.innerHTML = '';
      selectedFiles.forEach((f, i) => {
        list.innerHTML += `
          <div class="file-row" id="row-${i}">
            <span class="file-name">${f.name}</span>
            <span class="file-size">${formatBytes(f.size)}</span>
            <div class="progress-bar"><div class="progress-fill" id="prog-${i}"></div></div>
            <span class="file-status status-waiting" id="status-${i}">等待中</span>
          </div>`;
      });
      list.hidden = false;
      document.getElementById('start-btn').hidden = false;
    }

    function setStatus(i, text, cls) {
      const el = document.getElementById(`status-${i}`);
      el.textContent = text;
      el.className = `file-status ${cls}`;
    }

    function setProgress(i, pct) {
      document.getElementById(`prog-${i}`).style.width = pct + '%';
    }

    async function uploadFile(file, index) {
      setStatus(index, '初始化...', 'status-uploading');

      // 1. Init
      const initRes = await fetch('/upload/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: file.name,
          size_bytes: file.size,
          content_type: file.type || 'application/octet-stream',
        }),
      });
      if (!initRes.ok) {
        const err = await initRes.json();
        setStatus(index, `錯誤: ${err.detail || initRes.status}`, 'status-error');
        return false;
      }
      const { upload_id, minio_key, part_size, parts } = await initRes.json();

      // 2. Upload parts directly to MinIO
      const completedParts = [];
      for (const part of parts) {
        const start = (part.part_number - 1) * part_size;
        const end = Math.min(start + part_size, file.size);
        const chunk = file.slice(start, end);

        setStatus(index, `上傳中 ${part.part_number}/${parts.length}`, 'status-uploading');
        setProgress(index, Math.round((part.part_number - 1) / parts.length * 100));

        const putRes = await fetch(part.url, { method: 'PUT', body: chunk });
        if (!putRes.ok) {
          setStatus(index, `上傳失敗 (part ${part.part_number})`, 'status-error');
          return false;
        }
        const etag = putRes.headers.get('ETag') || putRes.headers.get('etag');
        completedParts.push({ part_number: part.part_number, etag });
        setProgress(index, Math.round(part.part_number / parts.length * 100));
      }

      // 3. Complete
      setStatus(index, '完成中...', 'status-uploading');
      const completeRes = await fetch('/upload/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id, minio_key, parts: completedParts }),
      });
      if (!completeRes.ok) {
        setStatus(index, '完成失敗', 'status-error');
        return false;
      }

      setProgress(index, 100);
      setStatus(index, '✓ 已加入佇列', 'status-done');
      return true;
    }

    document.getElementById('start-btn').addEventListener('click', async () => {
      const btn = document.getElementById('start-btn');
      btn.disabled = true;
      btn.textContent = '上傳中...';
      for (let i = 0; i < selectedFiles.length; i++) {
        await uploadFile(selectedFiles[i], i);
      }
      btn.textContent = '完成，前往 Dashboard 查看進度';
      btn.onclick = () => { window.location.href = '/'; };
      btn.disabled = false;
    });

    function addFiles(files) {
      const existing = new Set(selectedFiles.map(f => f.name));
      for (const f of files) {
        if (!existing.has(f.name)) selectedFiles.push(f);
      }
      renderFileList();
    }

    document.getElementById('file-input').addEventListener('change', e => {
      addFiles(e.target.files);
    });

    const dz = document.getElementById('drop-zone');
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
    dz.addEventListener('drop', e => {
      e.preventDefault();
      dz.classList.remove('drag-over');
      addFiles(e.dataTransfer.files);
    });
  </script>
</body>
</html>
```

- [ ] **Step 4: Manual test**

Start services and open browser:

```bash
bash scripts/ctl.sh start services
# open http://localhost:3000/upload
```

Verify:
- [ ] Upload page loads at `/upload`
- [ ] Drag-and-drop adds files to the list
- [ ] File picker works
- [ ] "upload" link appears in dashboard nav

- [ ] **Step 5: Commit**

```bash
git add web/templates/upload.html web/main.py web/templates/dashboard.html
git commit -m "feat(web): upload page with multipart JS + nav link"
```

---

## Task 10: Queue Panel — Dashboard Integration

**Files:**
- Create: `web/templates/partials/queue.html`
- Modify: `web/main.py` (add /partial/queue route)
- Modify: `web/templates/dashboard.html` (add queue panel)

- [ ] **Step 1: Add /partial/queue proxy route to web/main.py**

In `web/main.py`, add after the existing `/partial/status` route:

```python
@app.get("/partial/queue", response_class=HTMLResponse)
async def queue_partial(request: Request):
    tasks = await _get("/upload/queue")
    return templates.TemplateResponse(
        request=request,
        name="partials/queue.html",
        context={"tasks": tasks if isinstance(tasks, list) else []},
    )
```

- [ ] **Step 2: Create web/templates/partials/queue.html**

Create `web/templates/partials/queue.html`:

```html
{# HTMX partial — polled every 10 s to show upload queue status #}
{% if tasks %}
<div class="section">
  <div class="section-header">
    <span class="section-title">上傳佇列</span>
    <span class="section-count">{{ tasks|length }}</span>
  </div>
  <div class="task-list">
    {% for t in tasks %}
    <div class="task-row {% if t.status == 'completed' %}is-completed{% elif t.status == 'failed' %}is-failed{% elif t.status in ('processing', 'downloading') %}is-processing{% endif %}">

      {# Status indicator #}
      {% if t.status == 'completed' %}
        <span class="dot" style="background:#44aa44" title="completed">●</span>
      {% elif t.status == 'failed' %}
        <span class="dot" style="background:#cc4444" title="failed">●</span>
      {% elif t.status in ('processing', 'submitted') %}
        <span class="dot dot-processing" title="{{ t.status }}">●</span>
      {% elif t.status == 'downloading' %}
        <span class="dot" style="background:#5566cc" title="downloading">↓</span>
      {% else %}
        <span class="dot" style="background:#aaa" title="{{ t.status }}">○</span>
      {% endif %}

      <span class="task-stem">{{ t.stem }}</span>

      {% if t.current_stage %}
        <span class="task-stage">{{ t.current_stage }}</span>
      {% elif t.status == 'downloading' %}
        <span class="task-stage">MinIO 下載中...</span>
      {% elif t.status == 'pending' %}
        <span class="task-stage">等待佇列</span>
      {% endif %}

      {# Download links for completed #}
      {% if t.status == 'completed' and t.downloads %}
        <span class="task-links">
          {% if '.srt' in t.downloads %}
            <a href="{{ t.downloads['.srt'] }}" class="dl-link">⬇ SRT</a>
          {% endif %}
          {% if '_summary.md' in t.downloads %}
            <a href="{{ t.downloads['_summary.md'] }}" class="dl-link">⬇ 摘要</a>
          {% endif %}
        </span>
      {% endif %}

      {# Cancel button for pending only #}
      {% if t.status == 'pending' %}
        <form method="post" action="/upload/queue/{{ t.stem }}/cancel"
              style="margin:0" onsubmit="return confirm('取消 {{ t.stem }}？')">
          <button type="submit" class="cancel-btn">✕</button>
        </form>
      {% endif %}

      {% if t.error_msg %}
        <span class="task-error" title="{{ t.error_msg }}">⚠</span>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
```

- [ ] **Step 3: Add cancel route to web/main.py**

```python
@app.post("/upload/queue/{stem}/cancel", response_class=HTMLResponse)
async def cancel_upload_proxy(request: Request, stem: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(f"{API_URL}/upload/queue/{stem}")
    # Redirect back to dashboard
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=303)
```

- [ ] **Step 4: Add queue panel to dashboard.html**

In `web/templates/dashboard.html`, add the queue panel after the `status-region` div:

```html
    {# Upload queue panel — polled every 10 s #}
    <div id="queue-region"
         hx-get="/partial/queue"
         hx-trigger="every 10s"
         hx-swap="innerHTML">
    </div>
```

- [ ] **Step 5: Manual test**

```bash
# Services must be running
# Open http://localhost:3000
```

Verify:
- [ ] Queue panel appears on dashboard after upload
- [ ] Status updates every 10 s
- [ ] Completed tasks show download links
- [ ] Pending tasks show cancel button
- [ ] Cancel button removes task from queue

- [ ] **Step 6: Commit**

```bash
git add web/templates/partials/queue.html web/main.py web/templates/dashboard.html
git commit -m "feat(web): upload queue panel on dashboard with HTMX polling"
```

---

## Final: Run Full Test Suite + Push

- [ ] **Run all tests**

```bash
source venv/bin/activate
pytest tests/ -v --ignore=tests/fixtures 2>&1 | tail -20
```

Expected: all unit tests PASSED (smoke test requires live services, skip for now).

- [ ] **Push to origin**

```bash
git push origin main
```

---

## Env Vars Reference (docker-compose additions)

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_ENDPOINT` | `minio:9000` | MinIO S3 API endpoint (inside Docker network) |
| `MINIO_ACCESS_KEY` | `mediaflow` | MinIO root user |
| `MINIO_SECRET_KEY` | `changeme` | MinIO root password |
| `MINIO_SECURE` | `false` | Use HTTPS |
| `MINIO_INPUT_BUCKET` | `mediaflow-input` | Bucket for uploaded files |
| `MINIO_OUTPUT_BUCKET` | `mediaflow-output` | Bucket for SRT/summary backup |
| `UPLOAD_MAX_FILE_BYTES` | `5368709120` | 5 GB hard limit |
| `UPLOAD_PART_SIZE_BYTES` | `104857600` | 100 MB per multipart part |
| `UPLOAD_MAX_CONCURRENT` | `2` | Max simultaneous downloads/pipeline jobs |
