# CLI/API Task Management Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose pipeline task submission and management as a REST API so that external automation, AI agents, and scripts can submit files, monitor progress, retry failures, and cancel tasks — all visible and controllable from the web dashboard.

**Architecture:** A `POST /tasks` endpoint copies a local file into `workspace/1_input/` and creates a DB record immediately. Rerun requests are created via `POST /tasks/{stem}/runs` and written to a new SQLite `reruns` table. The pipeline watcher polls this table every 2 s on the host and executes the command in its existing thread pool. The Docker/host boundary is bridged through shared SQLite (already mounted) rather than Redis, preserving the existing rule that only `api/mq/consumer.py` touches Redis.

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

## 2. REST API Design

All endpoints live in `api/routes/tasks.py` (prefix `/tasks`). Follow standard REST conventions: `POST` creates a resource and returns **201 Created**, `DELETE` returns **200** with a confirmation body, errors return the appropriate 4xx with `{"detail": "..."}`.

### Resource model

```
/tasks              collection of pipeline tasks
/tasks/{stem}       single task resource
/tasks/{stem}/runs  runs (reruns) sub-collection for a task
/tasks/{stem}/timeline   (existing) stage timing for a task
```

---

### 2.1 `POST /tasks` — Create task from local path

Submit a host-local filesystem path to the pipeline. Intended for same-machine callers: automation scripts, AI agents, n8n workflows running on the same Mac mini.

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
6. Return 201 with task resource.

**Note:** The watcher will also publish `task.submitted` via Redis when it detects the file. The second upsert is idempotent (same stem, same status).

**Response 201:**
```json
{
  "stem": "recording",
  "filename": "recording.m4a",
  "status": "submitted",
  "submitted_at": 1749600000.0
}
```

**Errors:** `404` file not found · `415` unsupported format · `409` stem conflict

---

### 2.2 `POST /tasks/{stem}/runs` — Create a new run (rerun)

Create a new pipeline run for an existing task. Works for both failed and completed tasks. Queues the run via the `reruns` table; the watcher executes it on the host.

**Request body:**
```json
{"from_stage": "summarize"}
```
`from_stage` is optional. Omit or `null` for a full restart from `preprocess`.

**Valid stage ids:** `preprocess`, `transcribe`, `verify_segments`, `correct_srt`, `diarize`, `summarize`, `detect_chapters`. The endpoint validates against `runner.STAGE_RUNNERS.keys()` and returns 422 for unknown values.

**Logic:**
1. Load task from DB; 404 if not found.
2. Validate `from_stage` if provided.
3. Insert row into `reruns` table: `(stem, from_stage, requested_at)`.
4. `upsert_task(stem, status="submitted", error_msg=None)`.
5. Return 201 with run resource.

**Response 201:**
```json
{
  "stem": "recording",
  "from_stage": "summarize",
  "status": "submitted"
}
```

**Errors:** `404` task not found · `422` unknown stage

---

### 2.3 `DELETE /tasks/{stem}` — Delete/cancel task

Cancel a queued task or permanently remove a completed/failed record.

**Logic:**
1. Load task; 404 if not found.
2. If `filename` set: delete `workspace/1_input/{filename}` and `workspace/1_input/{filename}.failed` if either exists.
3. Delete DB row via existing `db.delete_task(stem)`.
4. Return 200 with confirmation.

**Response 200:**
```json
{"deleted": "recording"}
```

**Errors:** `404` task not found

---

### 2.4 Existing endpoints (unchanged)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tasks/{stem}/timeline` | Stage timing for a completed task |

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
async def insert_rerun(stem: str, from_stage: str | None) -> None:
    """Insert a rerun request into the reruns table."""

async def pop_oldest_rerun() -> dict | None:
    """Atomically select and delete the oldest pending rerun row."""
```

`pop_oldest_rerun` uses SELECT + DELETE in a single transaction to prevent double-execution if the watcher restarts mid-poll.

### 3.3 `tasks` table: `cancelled` status

No schema change required — `status TEXT` already accepts any string. `get_status_overview()` filters on known statuses so cancelled tasks disappear from the active view automatically.

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

SELECT + DELETE in one transaction prevents double-execution on restart:

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

`db_path` is read from environment: `os.getenv("DB_PATH", "./pipeline.db")`.

Start the poller thread before the watchdog loop, stop it on `KeyboardInterrupt`:

```python
import threading, os
db_path = os.getenv("DB_PATH", "./pipeline.db")
stop_ev = threading.Event()
poller = threading.Thread(target=_rerun_poller, args=(cfg, pub, db_path, stop_ev), daemon=True)
poller.start()
# ... existing observer loop ...
stop_ev.set()  # on shutdown
```

---

## 5. Web Layer Changes

### 5.1 `web/main.py` — proxy endpoints

```python
@app.post("/tasks", response_class=HTMLResponse)
async def submit_task_proxy(request: Request): ...
    # POST body → api /tasks, redirect to dashboard on success

@app.post("/tasks/{stem}/runs", response_class=HTMLResponse)
async def rerun_task_proxy(request: Request, stem: str): ...
    # POST to api, return refreshed accordion body via HTMX

@app.delete("/tasks/{stem}", response_class=HTMLResponse)
async def delete_task_proxy(request: Request, stem: str): ...
    # DELETE to api, return empty string so HTMX removes the row
```

### 5.2 Dashboard action buttons

`web/templates/partials/status.html` — action buttons per task status:

| Task status | UI action |
|-------------|-----------|
| `submitted` | Cancel button (`hx-delete`) |
| `processing` | — (no interrupt) |
| `failed` | "Retry" button (full rerun) + "Rerun from…" `<select>` |
| `completed` | "Rerun from…" `<select>` (visible in accordion detail) |

All destructive buttons use HTMX `hx-confirm`.

---

## 6. Operations Manual (`docs/operations-manual.md`)

Sections:

1. **Quick start** — three ways to submit a file (file drop, curl, web UI button)
2. **API reference** — every endpoint with request/response examples and error codes
3. **AI Agent / automation integration** — Python snippet, curl one-liners, n8n HTTP Request node example
4. **Rerun cookbook** — common scenarios: re-generate summary after prompt tuning; re-run from transcription when Whisper settings change; full restart after a failed download
5. **Troubleshooting** — common errors (409 conflict, 415 unsupported format, watcher not polling reruns) and fixes

---

## 7. Tests

New test file `tests/test_task_management.py`:

- `test_submit_valid_path` — happy path, returns 201 with task body
- `test_submit_missing_file` — 404
- `test_submit_unsupported_format` — 415
- `test_submit_conflict` — 409 when active task exists
- `test_rerun_inserts_db_row` — 201, `reruns` table row created, task status reset
- `test_rerun_unknown_stage` — 422
- `test_rerun_unknown_task` — 404
- `test_delete_task` — 200, row removed
- `test_delete_unknown` — 404

---

## 8. Out of Scope

- Cancel an in-progress (running) pipeline task — requires thread interruption; deferred.
- HTTP multipart file upload (covered by existing MinIO upload flow).
- Watcher health endpoint — separate concern.
