# CLI/API Task Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /tasks`, `POST /tasks/{stem}/runs`, and `DELETE /tasks/{stem}` REST endpoints so external automation and AI agents can submit files to the pipeline and manage them; add matching dashboard buttons for cancel (queued) and rerun (failed/completed).

**Architecture:** The API writes rerun requests to a new SQLite `reruns` table. The pipeline watcher (running on the host) polls this table every 2 s via sync `sqlite3` and executes reruns in its existing thread pool. Both sides share the same SQLite file (already mounted as a volume). No Redis on the API side — preserves existing architecture rule.

**Tech Stack:** FastAPI, aiosqlite (API), sqlite3 sync (watcher), HTMX 1.9.12 (dashboard buttons), Jinja2 templates.

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `api/db.py` | Modify | Add `reruns` table to SCHEMA + `import time` + `insert_rerun()` + `pop_oldest_rerun()` |
| `api/routes/tasks.py` | Modify | Add `POST /tasks`, `POST /tasks/{stem}/runs`, `DELETE /tasks/{stem}` |
| `pipeline/watcher.py` | Modify | Add `_run_rerun()`, `_rerun_poller()`, update `run()` |
| `web/main.py` | Modify | Add `/tasks/{stem}/runs` and `/tasks/{stem}` proxy endpoints |
| `web/templates/partials/status.html` | Modify | Cancel button on queued rows; rerun form on failed rows |
| `web/templates/partials/task_detail.html` | Modify | Rerun-from-stage form in completed accordion |
| `web/static/style.css` | Modify | Add `.btn-sm`, `.btn-danger`, `.btn-warning`, `.task-actions` |
| `tests/test_task_management.py` | Create | All new tests |
| `docs/operations-manual.md` | Create | Operations reference manual |

---

## Task 1: DB — `reruns` table + async helpers

**Files:**
- Modify: `api/db.py`
- Test: `tests/test_task_management.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_task_management.py`:

```python
"""Tests for task management API — submission, rerun, delete."""
import asyncio
import os
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


def test_reruns_table_exists(tmp_db):
    """Schema migration creates reruns table with expected columns."""
    import aiosqlite

    async def check():
        async with aiosqlite.connect(tmp_db.DB_PATH) as db:
            cur = await db.execute("PRAGMA table_info(reruns)")
            cols = {row[1] for row in await cur.fetchall()}
        assert "stem" in cols
        assert "from_stage" in cols
        assert "requested_at" in cols

    asyncio.get_event_loop().run_until_complete(check())


def test_insert_and_pop_rerun(tmp_db):
    """insert_rerun persists a row; pop_oldest_rerun returns and removes it."""
    async def run():
        await tmp_db.insert_rerun("s1", "summarize")
        row = await tmp_db.pop_oldest_rerun()
        assert row is not None
        assert row["stem"] == "s1"
        assert row["from_stage"] == "summarize"
        empty = await tmp_db.pop_oldest_rerun()
        assert empty is None

    asyncio.get_event_loop().run_until_complete(run())


def test_pop_returns_fifo_order(tmp_db):
    """pop_oldest_rerun returns the earliest-inserted row first."""
    async def run():
        await tmp_db.insert_rerun("first", None)
        await tmp_db.insert_rerun("second", "summarize")
        row = await tmp_db.pop_oldest_rerun()
        assert row["stem"] == "first"

    asyncio.get_event_loop().run_until_complete(run())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source venv/bin/activate
pytest tests/test_task_management.py -v
```
Expected: `FAILED — AttributeError: module 'api.db' has no attribute 'insert_rerun'`

- [ ] **Step 3: Add `reruns` table + `import time` to `api/db.py`**

At the top of `api/db.py` (line 3, after `from pathlib import Path`), add:
```python
import time
```

In the `SCHEMA` string (after the `events` table block, before the closing `"""`), add:
```sql

CREATE TABLE IF NOT EXISTS reruns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stem          TEXT NOT NULL,
    from_stage    TEXT,
    requested_at  REAL NOT NULL
);
```

At the bottom of `api/db.py`, add:
```python
async def insert_rerun(stem: str, from_stage: "str | None") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reruns (stem, from_stage, requested_at) VALUES (?, ?, ?)",
            (stem, from_stage, time.time()),
        )
        await db.commit()


async def pop_oldest_rerun() -> "dict | None":
    """Atomically pop the oldest rerun request. Returns None if queue is empty."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM reruns ORDER BY requested_at ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM reruns WHERE id = ?", (row["id"],))
            await db.commit()
        return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_task_management.py::test_reruns_table_exists \
       tests/test_task_management.py::test_insert_and_pop_rerun \
       tests/test_task_management.py::test_pop_returns_fifo_order -v
```
Expected: 3 PASSED

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/ -v --tb=short
```
Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add api/db.py tests/test_task_management.py
git commit -m "feat(api): add reruns table and async helpers to db"
```

---

## Task 2: API endpoints — `POST /tasks`, `POST /tasks/{stem}/runs`, `DELETE /tasks/{stem}`

**Files:**
- Modify: `api/routes/tasks.py`
- Test: `tests/test_task_management.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_task_management.py`:

```python
# ── API endpoint fixtures ────────────────────────────────────────────────────

@pytest.fixture
def tasks_client(tmp_path, monkeypatch):
    """TestClient wired to a fresh DB and a real workspace/1_input/ dir."""
    db_file = str(tmp_path / "test.db")
    ws = tmp_path / "workspace"
    (ws / "1_input").mkdir(parents=True)

    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("WORKSPACE_DIR", str(ws))

    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())

    import api.routes.tasks as tasks_mod
    importlib.reload(tasks_mod)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(tasks_mod.router)
    return TestClient(app)


# ── POST /tasks ──────────────────────────────────────────────────────────────

def test_submit_valid_path_returns_201(tasks_client, tmp_path):
    src = tmp_path / "recording.m4a"
    src.write_bytes(b"fake audio")
    ws = tmp_path / "workspace"

    resp = tasks_client.post("/tasks", json={"path": str(src)})
    assert resp.status_code == 201
    data = resp.json()
    assert data["stem"] == "recording"
    assert data["status"] == "submitted"
    assert data["filename"] == "recording.m4a"
    assert (ws / "1_input" / "recording.m4a").exists()


def test_submit_missing_file_returns_404(tasks_client, tmp_path):
    resp = tasks_client.post("/tasks", json={"path": "/nonexistent/file.m4a"})
    assert resp.status_code == 404


def test_submit_unsupported_format_returns_415(tasks_client, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"fake pdf")
    resp = tasks_client.post("/tasks", json={"path": str(src)})
    assert resp.status_code == 415


def test_submit_conflict_returns_409(tasks_client, tmp_path):
    import api.db as db_mod
    src = tmp_path / "lesson.m4a"
    src.write_bytes(b"audio")
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("lesson", status="processing", filename="lesson.m4a")
    )
    resp = tasks_client.post("/tasks", json={"path": str(src)})
    assert resp.status_code == 409


def test_submit_stem_override(tasks_client, tmp_path):
    src = tmp_path / "recording.m4a"
    src.write_bytes(b"audio")
    resp = tasks_client.post("/tasks", json={"path": str(src), "stem": "custom_name"})
    assert resp.status_code == 201
    assert resp.json()["stem"] == "custom_name"


# ── POST /tasks/{stem}/runs ──────────────────────────────────────────────────

def test_rerun_inserts_db_row_and_returns_201(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s1", status="failed", filename="s1.m4a")
    )
    resp = tasks_client.post("/tasks/s1/runs", json={"from_stage": "summarize"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["stem"] == "s1"
    assert data["from_stage"] == "summarize"
    assert data["status"] == "submitted"

    row = asyncio.get_event_loop().run_until_complete(db_mod.pop_oldest_rerun())
    assert row["stem"] == "s1"
    assert row["from_stage"] == "summarize"


def test_rerun_full_restart_omits_from_stage(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s2", status="failed", filename="s2.m4a")
    )
    resp = tasks_client.post("/tasks/s2/runs", json={})
    assert resp.status_code == 201
    assert resp.json()["from_stage"] is None


def test_rerun_unknown_stage_returns_422(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s3", status="failed", filename="s3.m4a")
    )
    resp = tasks_client.post("/tasks/s3/runs", json={"from_stage": "nonexistent"})
    assert resp.status_code == 422


def test_rerun_unknown_task_returns_404(tasks_client):
    resp = tasks_client.post("/tasks/ghost/runs", json={})
    assert resp.status_code == 404


# ── DELETE /tasks/{stem} ─────────────────────────────────────────────────────

def test_delete_removes_db_row(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("to_del", status="failed", filename="to_del.m4a")
    )
    resp = tasks_client.delete("/tasks/to_del")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "to_del"}
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("to_del"))
    assert task is None


def test_delete_removes_file_from_input(tasks_client, tmp_path, monkeypatch):
    import api.db as db_mod
    ws = tmp_path / "workspace"
    f = ws / "1_input" / "queued.m4a"
    f.write_bytes(b"audio")
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("queued", status="submitted", filename="queued.m4a")
    )
    tasks_client.delete("/tasks/queued")
    assert not f.exists()


def test_delete_unknown_returns_404(tasks_client):
    resp = tasks_client.delete("/tasks/ghost")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_task_management.py -k "submit or rerun or delete" -v
```
Expected: FAILED — routes not yet implemented

- [ ] **Step 3: Implement the new endpoints in `api/routes/tasks.py`**

Replace the entire file contents with:

```python
"""Task management — submit, rerun, delete, and timeline."""
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import db

router = APIRouter(prefix="/tasks")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
SUPPORTED_FORMATS = set(
    os.getenv("PIPELINE_SUPPORTED_FORMATS", ".mp4,.m4a,.mp3,.wav,.flac").split(",")
)
_VALID_STAGES = frozenset({
    "preprocess", "transcribe", "verify_segments", "correct_srt",
    "diarize", "summarize", "detect_chapters",
})
_ACTIVE_STATUSES = {"pending", "downloading", "queued", "submitted", "processing"}


def _stem_from_filename(filename: str) -> str:
    name = filename.rsplit(".", 1)[0]
    name = re.sub(r"[^\w\-]", "_", name.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "upload"


class SubmitRequest(BaseModel):
    path: str
    stem: Optional[str] = None


class RunRequest(BaseModel):
    from_stage: Optional[str] = None


@router.post("", status_code=201)
async def submit_task(req: SubmitRequest):
    """Create a task from a host-local file path. For automation/AI callers on the same machine."""
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if path.suffix not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported format: {path.suffix!r}. Supported: {sorted(SUPPORTED_FORMATS)}",
        )

    stem = req.stem or _stem_from_filename(path.name)
    existing = await db.get_task(stem)
    if existing and existing["status"] in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Task {stem!r} already active (status={existing['status']})",
        )

    dest = WORKSPACE / "1_input" / path.name
    shutil.copy2(path, dest)

    ts = time.time()
    await db.upsert_task(stem, filename=path.name, status="submitted", submitted_at=ts)
    return {"stem": stem, "filename": path.name, "status": "submitted", "submitted_at": ts}


@router.post("/{stem}/runs", status_code=201)
async def create_run(stem: str, req: RunRequest):
    """Queue a new pipeline run for an existing task. from_stage=null means full restart."""
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if req.from_stage and req.from_stage not in _VALID_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown stage: {req.from_stage!r}. Valid: {sorted(_VALID_STAGES)}",
        )

    await db.insert_rerun(stem, req.from_stage)
    await db.upsert_task(stem, status="submitted", error_msg=None)
    return {"stem": stem, "from_stage": req.from_stage, "status": "submitted"}


@router.delete("/{stem}")
async def delete_task_route(stem: str):
    """Delete a task record and remove any queued input file."""
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("filename"):
        for candidate in [
            WORKSPACE / "1_input" / task["filename"],
            WORKSPACE / "1_input" / (task["filename"] + ".failed"),
        ]:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass

    await db.delete_task(stem)
    return {"deleted": stem}


@router.get("/{stem}/timeline")
async def get_timeline(stem: str):
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    stage_events = await db.get_stage_events(stem)

    submitted = task.get("submitted_at")
    prev_ts = submitted
    stage_list = []
    for ev in stage_events:
        ts = ev["ts"]
        duration = round(ts - prev_ts) if prev_ts is not None else None
        stage_list.append({
            "stage": ev["stage"],
            "completed_at": ts,
            "duration_sec": duration,
        })
        prev_ts = ts

    total_pipeline = sum(
        s["duration_sec"] for s in stage_list if s["duration_sec"] is not None
    )
    completed = task.get("completed_at")
    total_wall = round(completed - submitted) if completed and submitted else None

    return {
        "stem": stem,
        "filename": task.get("filename"),
        "submitted_at": submitted,
        "started_at": task.get("started_at"),
        "completed_at": completed,
        "total_pipeline_sec": total_pipeline,
        "total_wall_sec": total_wall,
        "stages": stage_list,
    }
```

- [ ] **Step 4: Run the new endpoint tests**

```bash
pytest tests/test_task_management.py -v
```
Expected: all tests PASS (including DB tests from Task 1)

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v --tb=short
```
Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add api/routes/tasks.py tests/test_task_management.py
git commit -m "feat(api): add POST /tasks, POST /tasks/{stem}/runs, DELETE /tasks/{stem}"
```

---

## Task 3: Watcher — rerun poller

**Files:**
- Modify: `pipeline/watcher.py`

No automated unit tests — the watcher is a process that owns threads and a blocking observer. Manual integration test instructions are at the end of this task.

- [ ] **Step 1: Add imports to `pipeline/watcher.py`**

Current imports (lines 1-18):
```python
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
...
```

Add `import os`, `import sqlite3`, `import threading`, `import time` after line 1:

```python
import logging
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from pipeline.config import load, workspace
from pipeline.mq.publisher import EventPublisher
from pipeline import runner
```

- [ ] **Step 2: Add `_run_rerun` function**

Add after the `_mark_failed` function (after line 59), before `class InputHandler`:

```python
def _run_rerun(stem: str, from_stage: "str | None", cfg: dict, pub: EventPublisher) -> None:
    """Execute a rerun command dispatched from the reruns DB table."""
    from pipeline.rerun import rerun
    try:
        rerun(stem, from_stage or "preprocess", cfg, pub)
    except Exception as exc:
        log.error("Rerun FAILED for %s: %s", stem, exc)
        pub.publish("task.failed", stem, error_msg=str(exc))
```

- [ ] **Step 3: Add `_rerun_poller` function**

Add immediately after `_run_rerun`:

```python
def _rerun_poller(
    cfg: dict,
    pub: EventPublisher,
    db_path: str,
    stop_event: threading.Event,
) -> None:
    """Poll the reruns table every 2 s and dispatch work to the thread pool."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    log.info("Rerun poller started (db=%s)", db_path)
    while not stop_event.is_set():
        try:
            with conn:
                cur = conn.execute(
                    "SELECT * FROM reruns ORDER BY requested_at ASC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    conn.execute("DELETE FROM reruns WHERE id = ?", (row["id"],))
            if row:
                log.info("Rerun queued: stem=%s from_stage=%s", row["stem"], row["from_stage"])
                _executor.submit(_run_rerun, row["stem"], row["from_stage"], cfg, pub)
        except Exception as exc:
            log.error("Rerun poller error: %s", exc)
        time.sleep(2)
    conn.close()
    log.info("Rerun poller stopped")
```

- [ ] **Step 4: Update `run()` to start/stop the poller thread**

In `run()` (currently lines 91-119), make these changes:

1. After `pub = EventPublisher(cfg)`, add:
```python
    db_path = os.getenv("DB_PATH", "./data/pipeline.db")
    stop_ev = threading.Event()
    poller = threading.Thread(
        target=_rerun_poller,
        args=(cfg, pub, db_path, stop_ev),
        daemon=True,
        name="rerun-poller",
    )
    poller.start()
```

2. In the `except KeyboardInterrupt` block, add `stop_ev.set()` before `observer.stop()`:
```python
    except KeyboardInterrupt:
        stop_ev.set()
        observer.stop()
```

The complete updated `run()` function:
```python
def run():
    cfg = load()
    pub = EventPublisher(cfg)
    input_dir = workspace(cfg, "1_input")
    input_dir.mkdir(parents=True, exist_ok=True)

    db_path = os.getenv("DB_PATH", "./data/pipeline.db")
    stop_ev = threading.Event()
    poller = threading.Thread(
        target=_rerun_poller,
        args=(cfg, pub, db_path, stop_ev),
        daemon=True,
        name="rerun-poller",
    )
    poller.start()

    handler = InputHandler(cfg, pub)
    observer = Observer()
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()

    log.info("Watching %s", input_dir)

    for f in sorted(input_dir.iterdir()):
        if f.name.endswith(".failed"):
            continue
        if f.suffix in cfg["pipeline"]["supported_formats"]:
            log.info("Recovering on startup: %s", f.name)
            handler._submit(f)

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        stop_ev.set()
        observer.stop()

    observer.join()
    _executor.shutdown(wait=False)
```

- [ ] **Step 5: Manual smoke test**

With services running (`bash scripts/start-services.sh`) and the pipeline watcher active (`bash scripts/start-pipeline.sh`):

```bash
# 1. Trigger a rerun via the API
curl -s -X POST http://localhost:8080/tasks/YOUR_STEM/runs \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "summarize"}' | python3 -m json.tool
# Expected: {"stem": "...", "from_stage": "summarize", "status": "submitted"}

# 2. Watch the watcher log — should see within 2 s:
# INFO  Rerun queued: stem=YOUR_STEM from_stage=summarize
# INFO  ... summarize stage starting ...

# 3. Check DB status
curl -s http://localhost:8080/status/ | python3 -m json.tool
# Task should appear in "processing" then move to "completed"
```

Replace `YOUR_STEM` with a stem that has a completed task in `workspace/3_output/` (e.g., `test-speech`).

- [ ] **Step 6: Commit**

```bash
git add pipeline/watcher.py
git commit -m "feat(pipeline): add rerun poller thread to watcher"
```

---

## Task 4: Web proxies + dashboard action buttons

**Files:**
- Modify: `web/main.py`
- Modify: `web/templates/partials/status.html`
- Modify: `web/templates/partials/task_detail.html`
- Modify: `web/static/style.css`

- [ ] **Step 1: Add proxy endpoints to `web/main.py`**

Add after the `cancel_upload_proxy` endpoint (around line 234), before the `/health` endpoint:

```python
@app.post("/tasks/{stem}/runs", response_class=HTMLResponse)
async def rerun_proxy(request: Request, stem: str):
    """Dashboard rerun button — proxies to API POST /tasks/{stem}/runs."""
    from html import escape
    form = await request.form()
    from_stage = form.get("from_stage") or None
    await _post_json(f"/tasks/{stem}/runs", {"from_stage": from_stage})
    # Return a placeholder submitted row; the 5 s poll will replace it
    s = escape(stem)
    return HTMLResponse(
        f'<div class="task-row" id="task-row-{s}">'
        f'<span class="dot dot-queued"></span>'
        f'<span class="task-stem">{s}</span>'
        f'<span class="task-stage"><span class="stage-label">queued</span></span>'
        f'</div>'
    )


@app.delete("/tasks/{stem}", response_class=HTMLResponse)
async def delete_task_web(request: Request, stem: str):
    """Dashboard cancel button — proxies to API DELETE /tasks/{stem}."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(f"{API_URL}/tasks/{stem}")
    return HTMLResponse("")  # empty outerHTML swap removes the row
```

- [ ] **Step 2: Add button CSS to `web/static/style.css`**

Append at the end of `web/static/style.css`:

```css
/* ── Task action buttons ─────────────────────────────────── */
.btn-sm {
  font-family: var(--mono);
  font-size: .68rem;
  padding: 2px 7px;
  border-radius: var(--radius);
  border: 1px solid;
  cursor: pointer;
  background: transparent;
  transition: background var(--transition);
  line-height: 1.6;
}
.btn-danger  { color: var(--red);   border-color: var(--red-dim); }
.btn-danger:hover  { background: var(--red-dim); }
.btn-warning { color: var(--amber); border-color: var(--amber-dim); }
.btn-warning:hover { background: var(--amber-glow); }

.task-actions {
  display: flex;
  align-items: center;
  gap: .35rem;
  margin-left: auto;
  flex-shrink: 0;
}
.task-actions select {
  font-family: var(--mono);
  font-size: .68rem;
  background: var(--bg-card-alt);
  color: var(--text-mid);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2px 4px;
}
```

- [ ] **Step 3: Add cancel button to Queue section in `web/templates/partials/status.html`**

Find the Queue section (lines 65-74):
```html
    {% for t in queue %}
    <div class="task-row">
      <span class="dot dot-queued"></span>
      <span class="task-stem">{{ t.stem }}</span>
      <span class="task-stage"><span class="stage-label">waiting</span></span>
      <span class="task-time">{{ t.filename or '—' }}</span>
    </div>
    {% endfor %}
```

Replace with:
```html
    {% for t in queue %}
    <div class="task-row" id="task-row-{{ t.stem }}">
      <span class="dot dot-queued"></span>
      <span class="task-stem">{{ t.stem }}</span>
      <span class="task-stage"><span class="stage-label">waiting</span></span>
      <span class="task-time">{{ t.filename or '—' }}</span>
      <button class="btn-sm btn-danger"
              hx-delete="/tasks/{{ t.stem }}"
              hx-target="#task-row-{{ t.stem }}"
              hx-swap="outerHTML"
              hx-confirm="Cancel {{ t.stem }}?">cancel</button>
    </div>
    {% endfor %}
```

- [ ] **Step 4: Add rerun form to Failed section in `web/templates/partials/status.html`**

Find the Failed section (lines 119-128):
```html
    {% for t in failed %}
    <div class="task-row">
      <span class="dot dot-failed"></span>
      <span class="task-stem">{{ t.stem }}</span>
      <span class="task-stage"><span class="stage-label">{{ t.current_stage or '—' }}</span></span>
      <span class="task-time" title="{{ t.error_msg or '' }}">error</span>
    </div>
    {% endfor %}
```

Replace with:
```html
    {% for t in failed %}
    <div class="task-row" id="task-row-{{ t.stem }}">
      <span class="dot dot-failed"></span>
      <span class="task-stem">{{ t.stem }}</span>
      <span class="task-stage"><span class="stage-label">{{ t.current_stage or '—' }}</span></span>
      <span class="task-time" title="{{ t.error_msg or '' }}">error</span>
      <form class="task-actions"
            hx-post="/tasks/{{ t.stem }}/runs"
            hx-target="#task-row-{{ t.stem }}"
            hx-swap="outerHTML"
            hx-include="this"
            hx-confirm="Re-run {{ t.stem }}?">
        <select name="from_stage">
          <option value="">full restart</option>
          <option value="transcribe">from transcribe</option>
          <option value="summarize">from summarize</option>
        </select>
        <button class="btn-sm btn-warning" type="submit">run</button>
      </form>
    </div>
    {% endfor %}
```

- [ ] **Step 5: Add rerun form to completed task accordion in `web/templates/partials/task_detail.html`**

Append before the closing `</div>` of `.accordion-content` (after line 51, the `</div>` at the end):

```html
  <div class="accordion-actions" style="margin-top: 1rem; padding-top: .75rem; border-top: 1px solid var(--border);">
    <form class="task-actions"
          hx-post="/tasks/{{ stem }}/runs"
          hx-target="#task-row-{{ stem }}"
          hx-swap="outerHTML"
          hx-include="this"
          hx-confirm="Re-run {{ stem }}?">
      <span style="font-size:.72rem; color: var(--text-dim);">rerun from:</span>
      <select name="from_stage">
        <option value="">full restart</option>
        <option value="transcribe">transcribe</option>
        <option value="summarize">summarize</option>
        <option value="detect_chapters">detect_chapters</option>
      </select>
      <button class="btn-sm btn-warning" type="submit">run</button>
    </form>
  </div>
```

Note: `hx-target="#task-row-{{ stem }}"` targets the `<details>` element (which we add an id to in the next step).

- [ ] **Step 6: Add `id` to the completed task accordion `<details>` element**

In `web/templates/partials/status.html`, find the accordion `<details>` (line 87):
```html
    <details class="task-accordion"
             hx-get="/partial/task-detail/{{ t.stem }}"
```

Add `id="task-row-{{ t.stem }}"`:
```html
    <details class="task-accordion"
             id="task-row-{{ t.stem }}"
             hx-get="/partial/task-detail/{{ t.stem }}"
```

- [ ] **Step 7: Rebuild and smoke test in browser**

```bash
# Rebuild web container (only web changed)
docker compose build web && docker compose up -d web
# OR
podman-compose build web && podman-compose up -d web
```

Open http://localhost:3000. Verify:
- Queue section shows "cancel" button next to waiting tasks
- Failed section shows a stage select + "run" button
- Completed task accordion (expand one) shows rerun form at the bottom

- [ ] **Step 8: Commit**

```bash
git add web/main.py web/templates/partials/status.html \
        web/templates/partials/task_detail.html web/static/style.css
git commit -m "feat(web): cancel/rerun buttons on dashboard queue and failed rows"
```

---

## Task 5: Operations Manual

**Files:**
- Create: `docs/operations-manual.md`

- [ ] **Step 1: Create the manual**

Create `docs/operations-manual.md` with the following content:

````markdown
# mediaflow Operations Manual

API base URL: `http://localhost:8080`  
Web dashboard: `http://localhost:3000`

---

## Quick Start — Three Ways to Submit a File

### 1. File drop (CLI)
```bash
cp recording.m4a workspace/1_input/
```
The pipeline watcher picks it up within one second. The task appears in the dashboard immediately after the first Redis event.

### 2. API call (automation / AI agent)
```bash
curl -s -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"path": "/abs/path/to/recording.m4a"}' \
  | python3 -m json.tool
```
Response:
```json
{
  "stem": "recording",
  "filename": "recording.m4a",
  "status": "submitted",
  "submitted_at": 1749600000.0
}
```
The task appears on the dashboard immediately and the pipeline watcher picks up the copied file.

### 3. Web upload (browser)
Go to http://localhost:3000/upload and drag-and-drop or browse for a file. Supports files of any size via resumable multipart upload to MinIO.

---

## API Reference

All endpoints accept and return JSON. Error responses use `{"detail": "..."}`.

### Tasks

#### `POST /tasks` — Submit local file

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | ✓ | Absolute path to the audio/video file on the same machine |
| `stem` | string | — | Override the task name (derived from filename if omitted) |

**Supported formats:** `.mp4`, `.m4a`, `.mp3`, `.wav`, `.flac`

**Status codes:**
- `201` — Task created and file copied to `workspace/1_input/`
- `404` — File not found at `path`
- `415` — Unsupported file format
- `409` — A task with this stem is already active

```bash
# Minimal call
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/user/lecture.m4a"}'

# With custom stem
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/user/lecture.m4a", "stem": "lecture_2026_06"}'
```

---

#### `POST /tasks/{stem}/runs` — Create a new run

Queues a rerun for an existing task. The pipeline watcher executes it within 2 s.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from_stage` | string | — | Stage to restart from. Omit for full restart from `preprocess`. |

**Valid `from_stage` values:** `preprocess`, `transcribe`, `verify_segments`, `correct_srt`, `diarize`, `summarize`, `detect_chapters`

**Status codes:**
- `201` — Run queued
- `404` — Task not found
- `422` — Unknown stage name

```bash
# Full restart (re-runs all stages)
curl -X POST http://localhost:8080/tasks/lecture/runs \
  -H "Content-Type: application/json" \
  -d '{}'

# Re-run only summarize (transcript already exists)
curl -X POST http://localhost:8080/tasks/lecture/runs \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "summarize"}'
```

---

#### `DELETE /tasks/{stem}` — Delete task

Removes the task from the database and deletes the queued input file (if still in `workspace/1_input/`). Safe to call on completed or failed tasks for cleanup.

**Status codes:**
- `200` — Deleted
- `404` — Task not found

```bash
curl -X DELETE http://localhost:8080/tasks/lecture
```

---

#### `GET /tasks/{stem}/timeline` — Stage timing

Returns per-stage duration data for a completed task.

```bash
curl http://localhost:8080/tasks/lecture/timeline | python3 -m json.tool
```

```json
{
  "stem": "lecture",
  "total_wall_sec": 62,
  "stages": [
    {"stage": "preprocess",  "duration_sec": 8,  "completed_at": 1749600008.0},
    {"stage": "transcribe",  "duration_sec": 41, "completed_at": 1749600049.0},
    {"stage": "summarize",   "duration_sec": 13, "completed_at": 1749600062.0}
  ]
}
```

---

#### `GET /status/` — Pipeline overview

Returns all active, queued, recent, and failed tasks.

```bash
curl http://localhost:8080/status/ | python3 -m json.tool
```

---

### Upload (browser multipart — MinIO backed)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload/init` | POST | Start multipart upload, get presigned part URLs |
| `/upload/complete` | POST | Finalise upload and enqueue task |
| `/upload/queue` | GET | List upload-originated tasks |
| `/upload/queue/{stem}` | DELETE | Cancel a pending upload |

---

## AI Agent / Automation Integration

### Python script (same machine)

```python
import httpx

API = "http://localhost:8080"

def submit(path: str, stem: str | None = None) -> dict:
    resp = httpx.post(f"{API}/tasks", json={"path": path, "stem": stem})
    resp.raise_for_status()
    return resp.json()

def rerun(stem: str, from_stage: str | None = None) -> dict:
    resp = httpx.post(f"{API}/tasks/{stem}/runs", json={"from_stage": from_stage})
    resp.raise_for_status()
    return resp.json()

def wait_for_completion(stem: str, timeout: int = 600) -> dict:
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = httpx.get(f"{API}/status/").json()
        completed = {t["stem"]: t for t in resp.get("recent", [])}
        failed = {t["stem"]: t for t in resp.get("failed", [])}
        if stem in completed:
            return {"status": "completed", **completed[stem]}
        if stem in failed:
            return {"status": "failed", **failed[stem]}
        time.sleep(5)
    raise TimeoutError(f"Task {stem!r} did not complete within {timeout}s")

# Usage
task = submit("/recordings/meeting_2026_06_10.m4a")
print(task)  # {"stem": "meeting_2026_06_10", "status": "submitted", ...}
result = wait_for_completion(task["stem"])
print(result["status"])  # "completed"
```

### curl one-liners

```bash
# Submit, poll until done, print summary path
STEM=$(curl -sf -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"path": "/recordings/file.m4a"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['stem'])")

echo "Submitted: $STEM"

until curl -sf http://localhost:8080/status/ | python3 -c \
  "import sys, json; d=json.load(sys.stdin); \
   exit(0 if any(t['stem']=='$STEM' for t in d['recent']) else 1)"; do
  echo "Waiting..."
  sleep 10
done

echo "Done. Summary: workspace/3_output/${STEM}_summary.md"
```

### n8n HTTP Request node

1. **Method:** POST  
2. **URL:** `http://localhost:8080/tasks`  
3. **Body (JSON):**
   ```json
   {"path": "{{ $json.file_path }}"}
   ```
4. Connect to a **Wait** node (5 s interval) that polls `GET /status/` until `stem` appears in `recent`.

---

## Rerun Cookbook

### Re-generate summary after prompt tuning

Edit `pipeline/prompts.yaml`, then:
```bash
curl -X POST http://localhost:8080/tasks/STEM/runs \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "summarize"}'
```
Skips FFmpeg + Whisper. Reruns only `summarize` (and `detect_chapters` if enabled).

### Re-run from transcription (new Whisper settings)

Ensure `workspace/2_processing/STEM_clean.wav` exists (it does unless you deleted it), then:
```bash
curl -X POST http://localhost:8080/tasks/STEM/runs \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "transcribe"}'
```

### Full restart after a failed download

If a task failed mid-way and the original file is in `workspace/4_archive/`:
```bash
curl -X POST http://localhost:8080/tasks/STEM/runs \
  -H "Content-Type: application/json" \
  -d '{}'
```
`rerun.py` searches both `4_archive/` and `1_input/` for the original file.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `409 Task 'X' already active` | A task with that stem is still running | Wait for it to complete, or `DELETE /tasks/X` first |
| `404 File not found` | Path doesn't exist on the host, or the API container can't see it | The file must be on the same machine; the API reads `WORKSPACE_DIR` and your file path directly |
| `415 Unsupported format` | File extension not in `.mp4,.m4a,.mp3,.wav,.flac` | Convert the file first, or add the extension to `PIPELINE_SUPPORTED_FORMATS` env var |
| `422 Unknown stage` | Typo in `from_stage` | Valid values: `preprocess`, `transcribe`, `verify_segments`, `correct_srt`, `diarize`, `summarize`, `detect_chapters` |
| Rerun queued but nothing happens | Watcher not running, or `DB_PATH` env var mismatch | Start `bash scripts/start-pipeline.sh`; verify `DB_PATH` matches the mounted `data/pipeline.db` |
| Dashboard cancel button does nothing | Web container not rebuilt after code change | `docker compose build web && docker compose up -d web` |
````

- [ ] **Step 2: Commit**

```bash
git add docs/operations-manual.md
git commit -m "docs: add operations manual with API reference and integration examples"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
source venv/bin/activate
pytest tests/ -v
```
Expected: all tests pass. Note: watcher tests are manual only.

- [ ] **Rebuild all Docker containers** (API changed in Tasks 1–2)

```bash
docker compose build api web && docker compose up -d
# OR
podman-compose build api web && podman-compose up -d
```

- [ ] **End-to-end API smoke test**

```bash
# 1. Check API is up
curl http://localhost:8080/health
# {"status": "ok"}

# 2. Submit a file (adjust path to an actual file)
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/workspace/4_archive/test-speech.m4a"}'
# 201: {"stem": "test-speech", "status": "submitted", ...}

# 3. Watch dashboard at http://localhost:3000 — task should appear

# 4. After completion, rerun from summarize
curl -X POST http://localhost:8080/tasks/test-speech/runs \
  -H "Content-Type: application/json" \
  -d '{"from_stage": "summarize"}'
# 201: {"stem": "test-speech", "from_stage": "summarize", "status": "submitted"}

# 5. Delete
curl -X DELETE http://localhost:8080/tasks/test-speech
# 200: {"deleted": "test-speech"}
```
