# CLI/API Task Management Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose pipeline task submission and management as a REST API so that external automation, AI agents, and scripts can submit files, monitor progress, retry failures, and cancel tasks — all visible and controllable from the web dashboard.

**Architecture:** A `POST /tasks/submit` endpoint copies a local file into `workspace/1_input/` and creates a DB record immediately. Rerun and cancel commands write to a new SQLite `reruns` table. The pipeline watcher polls this table every 2 s on the host and executes the command in its existing thread pool. The Docker/host boundary is bridged through shared SQLite (already mounted) rather than Redis, preserving the existing rule that only `api/mq/consumer.py` touches Redis.

**Tech Stack:** FastAPI (api layer), aiosqlite (api DB), sqlite3 sync (watcher polling), HTMX (dashboard buttons), Jinja2 (templates).

---

## 1. Current State

| Path | What happens |
|------|-------------|
| Browser upload | Browser → MinIO → `tasks` DB (pending) → `queue_consumer` downloads to `1_input/` → watcher → Redis → DB |
| CLI file drop | File dropped in `1_input/` → watcher detects → Redis `task.submitted` → `consumer.py` → DB |
| `pipeline/rerun.py` | CLI-only; runs on host; publishes Redis events when done |

**Gaps closed by this feature:**
- No HTTP endpoint for external callers to submit a file
- Rerun is CLI-only (no web trigger)
- No cancel / delete from web UI
- No unified management API for AI agents / automation scripts

---

## 2. New API Endpoints

All endpoints live in the existing `api/routes/tasks.py` router (prefix `/tasks`).

### 2.1 `POST /tasks/submit`

Submit a local filesystem path to the pipeline.

**Request body:**
```json
{
  "path": "/abs/path/to/recording.m4a",
  "stem": "optional_override"
}
```

**Logic:**
1. Validate `path` exists and its suffix is in `config.pipeline.supported_formats`.
2. Derive `stem` from filename if not provided (same slug logic as upload route).
3. Check for stem conflict: if an active task exists (status not in `completed/failed/cancelled`), return 409.
4. Copy file to `workspace/1_input/{filename}` (shutil.copy2).
5. `upsert_task(stem, filename=..., status="submitted", submitted_at=now)`.
6. Return `{"stem": stem, "status": "submitted", "filename": filename}`.

**Note:** The watcher will also publish `task.submitted` via Redis when it detects the file. The second upsert is idempotent (same stem, same status).

**Response 200:**
```json
{"stem": "recording", "status": "submitted", "filename": "recording.m4a"}
```

**Errors:** 404 file not found, 415 unsupported format, 409 stem conflict.

---

### 2.2 `POST /tasks/{stem}/rerun`

Queue a rerun via the watcher. Works for both failed and completed tasks.

**Request body:**
```json
{"from_stage": "summarize"}
```
`from_stage` is optional. Omit or `null` for a full restart from `preprocess`.

**Valid stage ids:** `preprocess`, `transcribe`, `verify_segments`, `correct_srt`, `diarize`, `summarize`, `detect_chapters`. The endpoint validates the value against `runner.STAGE_RUNNERS.keys()` and returns 422 for unknown stages.

**Logic:**
1. Load task from DB; 404 if missing.
2. Insert row into `reruns` table: `(stem, from_stage, requested_at)`.
3. `upsert_task(stem, status="submitted", error_msg=None)`.
4. Return `{"stem": stem, "queued": True, "from_stage": from_stage}`.

**Response 200:**
```json
{"stem": "recording", "queued": true, "from_stage": "summarize"}
```

---

### 2.3 `DELETE /tasks/{stem}`

Cancel a queued task or permanently delete a completed/failed record.

**Logic:**
1. Load task; 404 if missing.
2. If `filename` set: iterate `workspace/1_input/` and delete files whose name is `{filename}` or `{filename}.failed`. Do not use broad glob to avoid accidentally matching other stems.
3. Delete DB row via existing `db.delete_task(stem)`.
4. Return `{"deleted": stem}`.

**Response 200:**
```json
{"deleted": "recording"}
```

---

## 3. Database Changes

### 3.1 New `reruns` table

Added to `api/db.py` SCHEMA string and `init()`:

```sql
CREATE TABLE IF NOT EXISTS reruns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stem          TEXT NOT NULL,
    from_stage    TEXT,
    requested_at  REAL NOT NULL
);
```

### 3.2 New DB helpers

```python
async def insert_rerun(stem: str, from_stage: str | None) -> None: ...
async def pop_oldest_rerun() -> dict | None: ...   # DELETE + return in one tx
```

`pop_oldest_rerun` uses a single transaction to prevent double-execution if the watcher restarts mid-poll.

### 3.3 `tasks` table: `cancelled` status

No schema change required — `status TEXT` already accepts any string. The `get_status_overview()` query filters on known statuses and will not surface `cancelled` rows, which is the correct behaviour (cancelled tasks disappear from the active view). Add `cancelled` to `_STATUS_MAP` in `event_processor.py` if needed.

---

## 4. Watcher Changes (`pipeline/watcher.py`)

### 4.1 New function: `_run_rerun`

Worker function submitted to the existing `_executor`:

```python
def _run_rerun(stem: str, from_stage: str | None, cfg: dict, pub: EventPublisher) -> None:
    from pipeline.rerun import rerun
    try:
        rerun(stem, from_stage or "preprocess", cfg, pub)
    except Exception as exc:
        pub.publish("task.failed", stem, error_msg=str(exc))
```

### 4.2 New function: `_rerun_poller`

Runs in a dedicated daemon thread. Uses **synchronous `sqlite3`** (no async) because the watcher is sync.
`db_path` comes from `os.getenv("DB_PATH", "./pipeline.db")` — same env var as the API container.

Pop is done as SELECT + DELETE in one transaction to prevent double-execution on restart:

```python
def _rerun_poller(cfg: dict, pub: EventPublisher, db_path: str, stop_event) -> None:
    import sqlite3, time
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    while not stop_event.is_set():
        row = None
        with conn:
            cur = conn.execute(
                "SELECT * FROM reruns ORDER BY requested_at ASC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                conn.execute("DELETE FROM reruns WHERE id = ?", (row["id"],))
        if row:
            _executor.submit(_run_rerun, dict(row)["stem"], dict(row)["from_stage"], cfg, pub)
        time.sleep(2)
    conn.close()
```

### 4.3 `run()` change

`DB_PATH` is read from environment: `db_path = os.getenv("DB_PATH", "./pipeline.db")`.

Start the poller thread before the watchdog loop; stop it on `KeyboardInterrupt`:

```python
import threading, os
db_path = os.getenv("DB_PATH", "./pipeline.db")
stop_ev = threading.Event()
poller = threading.Thread(target=_rerun_poller, args=(cfg, pub, db_path, stop_ev), daemon=True)
poller.start()
# ... existing observer loop ...
# on shutdown:
stop_ev.set()
```

---

## 5. Web Layer Changes

### 5.1 `web/main.py` — proxy endpoints

```python
@app.post("/tasks/{stem}/rerun", response_class=HTMLResponse)
async def rerun_task_proxy(request: Request, stem: str): ...
    # POST to api, then return refreshed accordion body

@app.delete("/tasks/{stem}", response_class=HTMLResponse)
async def delete_task_proxy(request: Request, stem: str): ...
    # DELETE to api, then return empty string (HTMX removes element)
```

### 5.2 Dashboard template buttons

`web/templates/partials/status.html` gets action buttons on each task row:

| Task status | UI action |
|-------------|-----------|
| `submitted` | Cancel button (hx-delete) |
| `processing` | — (no interrupt) |
| `failed` | "Retry" button (full rerun) + "Rerun from…" `<select>` |
| `completed` | "Rerun from…" `<select>` (visible in accordion) |

All buttons use HTMX with `hx-confirm` for destructive actions.

---

## 6. Operations Manual

A new file `docs/operations-manual.md` is created alongside the implementation. It covers:

1. **Quick start** — three ways to submit a file (file drop, curl, web UI)
2. **API reference** — every endpoint with request/response examples
3. **Dashboard guide** — screenshot-annotated walkthrough
4. **AI Agent / automation integration** — Python snippet, curl one-liners, n8n webhook node example
5. **Rerun cookbook** — common scenarios (re-generate summary, re-run from transcription after prompt tuning)
6. **Troubleshooting** — common errors and fixes

---

## 7. Tests

New test file `tests/test_task_management.py`:

- `test_submit_valid_path` — happy path submit
- `test_submit_missing_file` — 404
- `test_submit_unsupported_format` — 415
- `test_submit_conflict` — 409 when active task exists
- `test_rerun_inserts_db_row` — verify `reruns` table row created
- `test_rerun_unknown_task` — 404
- `test_delete_task` — row removed
- `test_delete_unknown` — 404

---

## 8. Out of Scope

- Cancel an in-progress (running) pipeline task — requires thread interruption; deferred.
- HTTP multipart file upload (covered by existing MinIO upload flow).
- Watcher health endpoint — separate concern.
