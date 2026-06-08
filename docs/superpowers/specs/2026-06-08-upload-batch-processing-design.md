# Upload + Batch Processing — Design Spec

**Date:** 2026-06-08
**Status:** Approved

---

## Overview

Add a web-based file upload flow and queue-managed batch processing to mediaflow. Large files (up to 5 GB) are uploaded directly to MinIO using presigned multipart URLs (bypassing the API). A queue consumer controls when files are downloaded to the local workspace and handed to the existing pipeline — no changes to `watcher.py` or the pipeline stages.

---

## Goals

- Web UI and REST API both support multi-file upload
- Files up to 5 GB handled via MinIO multipart upload
- Queue management: enqueue on upload, consume with concurrency cap, show status
- Pipeline watcher unchanged; outputs (SRT, summary) backed up to MinIO on completion
- MinIO runs inside the existing Docker Compose stack

---

## Architecture

### Data Flow

```
Upload phase
  Browser/API client
    → POST /upload/init             (API signs presigned URLs)
    → PUT parts directly to MinIO   (browser ↔ MinIO, API not in path)
    → POST /upload/complete          (browser confirms; API finalises multipart)
    → task created (status = pending)

Queue consume phase (API background loop, every 5 s)
  IF active tasks < max_concurrent:
    pick oldest pending task
    UPDATE status = 'downloading'
    download MinIO mediaflow-input/{key} → workspace/1_input/{filename}
    UPDATE status = 'queued'
    watcher.py detects file → runs pipeline (unchanged)

Pipeline + output backup phase
  watcher → Redis events → API consumer (unchanged)
  On task.completed event (new):
    upload workspace/3_output/{stem}.* → MinIO mediaflow-output/{stem}/
    UPDATE tasks SET minio_output_prefix, status = 'completed'
```

### Concurrency

- `max_concurrent` (default 2) mirrors the existing watcher `ThreadPoolExecutor(max_workers=2)`
- Active count = `COUNT(status IN ('downloading', 'queued', 'submitted', 'processing'))` — includes tasks waiting for watcher pickup so we don't over-schedule
- Queue consumer only starts the next download when a slot is free

---

## New Service: MinIO

Added to `docker-compose.yml`:

```yaml
minio:
  image: minio/minio
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: ${MINIO_ACCESS_KEY:-mediaflow}
    MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY:-changeme}
  volumes:
    - minio-data:/data
  ports:
    - "9000:9000"   # S3 API
    - "9001:9001"   # Web Console
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
    interval: 10s
    timeout: 5s
    retries: 5
```

**Buckets** (created automatically on API startup):
- `mediaflow-input` — original uploaded files
- `mediaflow-output` — SRT + summary backup per stem

---

## Database Schema Changes

```sql
-- api/db.py migrations (run on startup if columns missing)
ALTER TABLE tasks ADD COLUMN minio_input_key TEXT;
ALTER TABLE tasks ADD COLUMN minio_output_prefix TEXT;

-- Extended status values:
-- pending | downloading | queued | submitted | processing | completed | failed
```

`minio_input_key` format: `{stem}/{filename}` in bucket `mediaflow-input`
`minio_output_prefix` format: `{stem}/` in bucket `mediaflow-output`

---

## API Changes

### New file: `api/routes/upload.py`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload/init` | POST | Initiate multipart upload; returns presigned part URLs |
| `/upload/complete` | POST | Finalise multipart upload; task enters queue |
| `/upload/queue` | GET | List pending/active/recent tasks with status |
| `/upload/queue/{stem}` | DELETE | Cancel a pending task |

#### `POST /upload/init`

Request:
```json
{ "filename": "lecture_01.mp4", "size_bytes": 2252341248, "content_type": "video/mp4" }
```

Response:
```json
{
  "upload_id": "abc123",
  "minio_key": "lecture_01/lecture_01.mp4",
  "part_size": 104857600,
  "parts": [
    { "part_number": 1, "url": "https://..." },
    { "part_number": 2, "url": "https://..." }
  ]
}
```

- Part size: 100 MB → max 50 parts for a 5 GB file (well within MinIO's 10 000-part limit)
- Stem derived from filename: strip extension, lowercase, replace spaces/special chars with `_`
- Collision: if stem exists with status NOT IN (`completed`, `failed`) → return 409. Otherwise allow re-upload (new task row, old one archived)

#### `POST /upload/complete`

Request:
```json
{
  "upload_id": "abc123",
  "minio_key": "lecture_01/lecture_01.mp4",
  "parts": [
    { "part_number": 1, "etag": "\"abc\"" },
    { "part_number": 2, "etag": "\"def\"" }
  ]
}
```

- Calls `minio.complete_multipart_upload`
- Creates or updates task row: `status=pending`, `minio_input_key`, `queued_at`

#### `GET /upload/queue`

Returns all tasks where `minio_input_key IS NOT NULL` (i.e. upload-originated), ordered by `queued_at` desc. Includes status, progress hint, and presigned GET URLs (7-day expiry) for completed tasks' SRT + summary outputs.

#### `DELETE /upload/queue/{stem}`

- If `status=pending`: delete task row, abort MinIO multipart if still open
- If `status=downloading/queued/processing`: return 409 (cannot cancel mid-flight)

---

## New File: `api/mq/queue_consumer.py`

Asyncio background task started in `api/main.py` lifespan alongside the Redis consumer.

```
loop every 5 seconds:
  active = await db.count_active_tasks()   # downloading + processing
  if active < cfg["upload"]["max_concurrent"]:
    task = await db.pop_oldest_pending()
    if task:
      await db.update_status(task.stem, "downloading")
      await minio_client.download(task.minio_input_key,
                                  workspace / "1_input" / task.filename)
      await db.update_status(task.stem, "queued")
```

Error handling:
- MinIO download failure → `status=failed`, `error_msg` set
- File already exists in workspace → skip download, set `status=queued` directly

---

## Changes to Existing Files

### `api/event_processor.py`

On `task.completed` event, add output backup step:

```python
if event == "task.completed":
    await minio_client.upload_outputs(stem, output_dir)
    await db.update_minio_output_prefix(stem, f"{stem}/")
```

### `api/main.py`

- Start `queue_consumer` background task in lifespan
- Initialise MinIO client + ensure buckets exist on startup

### `api/db.py`

- Run schema migration on startup (add `minio_input_key`, `minio_output_prefix` columns)
- Add: `count_active_tasks()`, `pop_oldest_pending()`, `update_minio_output_prefix()`

### `docker-compose.yml`

- Add `minio` service (see above)
- Add `minio-data` to volumes

### `config.yaml.example`

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

---

## New Python Dependency

```
minio>=7.2          # Apache 2.0; S3-compatible client with presigned multipart support
```

Added to `requirements.txt`.

---

## Web UI Changes

### New page: `web/templates/upload.html`

- Drag-and-drop zone (multi-file)
- Per-file progress bar (multipart upload via `fetch` with chunked PUT)
- File list: name, size, status (waiting / uploading N% / done / error)
- "Start Upload" button; each file uploaded sequentially to avoid saturating bandwidth
- On completion, auto-navigates or shows link to Queue panel

Navigation: add "Upload" link to `base.html` nav.

### Updated: `web/templates/dashboard.html`

Add **Queue panel** (HTMX poll every 10 s against web route `GET /partial/queue`, which proxies `GET /upload/queue` from the API — same pattern as existing `/partial/status`):

| Field | Content |
|-------|---------|
| Status dot | ● processing / ↓ downloading / ○ pending / ✓ done |
| Filename stem | |
| Stage | e.g. "transcribing..." for processing tasks |
| Progress bar | pipeline or download progress |
| Cancel button | for pending tasks only |
| Download links | SRT + summary (MinIO presigned GET) for completed tasks |

---

## New Files Summary

| File | Purpose |
|------|---------|
| `api/routes/upload.py` | Upload init/complete/queue endpoints |
| `api/mq/queue_consumer.py` | Asyncio loop: pending → download → workspace |
| `api/minio_client.py` | MinIO wrapper (presigned URLs, upload, download, bucket init) |
| `web/templates/upload.html` | Upload UI with drag-and-drop + progress |

---

## Files Changed

| File | Change |
|------|--------|
| `docker-compose.yml` | Add MinIO service + volume |
| `config.yaml.example` | Add `minio` and `upload` sections |
| `requirements.txt` | Add `minio>=7.2` |
| `api/main.py` | Start queue consumer; init MinIO on lifespan |
| `api/db.py` | Schema migration + 3 new query helpers |
| `api/event_processor.py` | Backup outputs to MinIO on task.completed |
| `web/templates/dashboard.html` | Add Queue panel (HTMX) |
| `web/templates/base.html` | Add Upload nav link |
| `web/main.py` | Add `GET /partial/queue` proxy route |

---

## Out of Scope

- Authentication / access control on upload endpoints
- MinIO lifecycle policies (file expiry / retention)
- Resume interrupted uploads across browser sessions (upload_id is in-memory only)
- Direct streaming from MinIO to pipeline (pipeline always reads from local workspace)
