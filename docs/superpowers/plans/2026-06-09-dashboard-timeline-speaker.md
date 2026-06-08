# Dashboard Preview · Timeline · Speaker Save — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inline task preview to dashboard accordion, per-stage timing panels in dashboard + SRT viewer + JSON API, and visual confirmation after saving speaker names.

**Architecture:** Three independent layers — (1) two new API endpoints added to existing FastAPI routers, (2) three new Jinja2 partials + HTMX routes in the web service, (3) CSS additions for new UI components. The accordion uses HTMX's `toggle once` trigger on `<details>` elements. Speaker save confirmation uses HTMX OOB swap to update a `#save-status` span without changing the form submission target.

**Tech Stack:** FastAPI, aiosqlite, Jinja2, HTMX 1.9, IBM Plex Mono CSS design system (see `web/static/style.css` for variables: `--bg`, `--bg-card`, `--border`, `--amber`, `--green`, `--text`, `--text-dim`, `--mono`, `--radius`, `--transition`)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `api/db.py` | Modify | Add `get_stage_events(stem)` async function |
| `api/routes/tasks.py` | **Create** | `GET /tasks/{stem}/timeline` endpoint |
| `api/routes/files.py` | Modify | Add `GET /files/{stem}/summary` endpoint |
| `api/main.py` | Modify | Register `tasks` router |
| `web/static/style.css` | Modify | Accordion, timeline panel, save-success CSS |
| `web/main.py` | Modify | `_get_text` helper; 3 new routes; `datetimeformat` filter; update `save_speaker_names` |
| `web/templates/partials/status.html` | Modify | Wrap recent rows in `<details>` accordion |
| `web/templates/partials/task_detail.html` | **Create** | Accordion expanded content |
| `web/templates/partials/timeline.html` | **Create** | Stage timing panel for SRT viewer |
| `web/templates/partials/speaker_save_result.html` | **Create** | Transcript rows + OOB save confirmation |
| `web/templates/srt_viewer.html` | Modify | Add timeline region + `#save-status` span |
| `tests/test_timeline_route.py` | **Create** | Tests for `get_timeline`, `get_stage_events` |
| `tests/test_summary_route.py` | **Create** | Tests for `get_summary` |

---

## Task 1: Add `get_stage_events` to `api/db.py`

**Files:**
- Modify: `api/db.py`
- Create: `tests/test_timeline_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timeline_route.py
"""Tests for timeline DB helper and route logic."""
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


def test_get_stage_events_empty(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(
        tmp_db.get_stage_events("nostem")
    )
    assert result == []


def test_get_stage_events_returns_stage_completed_events(tmp_db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(tmp_db.insert_event(
        "s1", "stage.completed", stage="preprocess", ts=1000.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "s1", "task.submitted", stage="", ts=990.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "s1", "stage.completed", stage="transcribe", ts=1120.0, status="", payload="{}"
    ))
    result = loop.run_until_complete(tmp_db.get_stage_events("s1"))
    assert len(result) == 2
    assert result[0]["stage"] == "preprocess"
    assert result[0]["ts"] == 1000.0
    assert result[1]["stage"] == "transcribe"
    assert result[1]["ts"] == 1120.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
pytest tests/test_timeline_route.py -v 2>&1 | tail -15
```

Expected: `AttributeError: module 'api.db' has no attribute 'get_stage_events'`

- [ ] **Step 3: Add `get_stage_events` to `api/db.py`**

Add this function at the end of `api/db.py` (after `delete_task`):

```python
async def get_stage_events(stem: str) -> list:
    """Return stage.completed events for stem ordered by ts ascending."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT stage, ts FROM events "
            "WHERE stem = ? AND event = 'stage.completed' AND stage IS NOT NULL AND stage != '' "
            "ORDER BY ts ASC",
            (stem,),
        )
        return [dict(r) for r in await cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_timeline_route.py::test_get_stage_events_empty tests/test_timeline_route.py::test_get_stage_events_returns_stage_completed_events -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add api/db.py tests/test_timeline_route.py
git commit -m "feat(api): add get_stage_events DB helper + tests"
```

---

## Task 2: Create `api/routes/tasks.py` with timeline endpoint

**Files:**
- Create: `api/routes/tasks.py`
- Modify: `tests/test_timeline_route.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_timeline_route.py`

```python
# append to tests/test_timeline_route.py

def test_get_timeline_returns_404_for_missing_stem(tmp_db):
    import api.routes.tasks as tasks_mod
    import asyncio
    from unittest.mock import patch
    from fastapi import HTTPException

    async def run():
        with patch.object(tasks_mod, "db", tmp_db):
            try:
                await tasks_mod.get_timeline("nonexistent")
                return None
            except HTTPException as e:
                return e.status_code

    code = asyncio.get_event_loop().run_until_complete(run())
    assert code == 404


def test_get_timeline_computes_stage_durations(tmp_db):
    import api.routes.tasks as tasks_mod
    from unittest.mock import patch

    loop = asyncio.get_event_loop()

    loop.run_until_complete(tmp_db.upsert_task(
        "lesson01",
        filename="lesson01.m4a",
        status="completed",
        submitted_at=1000.0,
        started_at=1023.0,
        completed_at=1215.0,
        duration_sec=215.0,
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "lesson01", "stage.completed", stage="preprocess", ts=1023.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "lesson01", "stage.completed", stage="transcribe", ts=1180.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "lesson01", "stage.completed", stage="summarize", ts=1215.0, status="", payload="{}"
    ))

    async def run():
        with patch.object(tasks_mod, "db", tmp_db):
            return await tasks_mod.get_timeline("lesson01")

    result = loop.run_until_complete(run())
    assert result["stem"] == "lesson01"
    assert result["filename"] == "lesson01.m4a"
    assert result["submitted_at"] == 1000.0
    assert result["total_wall_sec"] == 215
    stages = result["stages"]
    assert len(stages) == 3
    # preprocess: 1023 - 1000 = 23s
    assert stages[0]["stage"] == "preprocess"
    assert stages[0]["duration_sec"] == 23
    # transcribe: 1180 - 1023 = 157s
    assert stages[1]["stage"] == "transcribe"
    assert stages[1]["duration_sec"] == 157
    # summarize: 1215 - 1180 = 35s
    assert stages[2]["stage"] == "summarize"
    assert stages[2]["duration_sec"] == 35
    assert result["total_pipeline_sec"] == 23 + 157 + 35
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_timeline_route.py::test_get_timeline_returns_404_for_missing_stem tests/test_timeline_route.py::test_get_timeline_computes_stage_durations -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'api.routes.tasks'`

- [ ] **Step 3: Create `api/routes/tasks.py`**

```python
"""Task timeline — per-stage timing for a completed task."""
from fastapi import APIRouter, HTTPException
from api import db

router = APIRouter(prefix="/tasks")


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

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_timeline_route.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add api/routes/tasks.py tests/test_timeline_route.py
git commit -m "feat(api): add GET /tasks/{stem}/timeline endpoint + tests"
```

---

## Task 3: Add summary endpoint to `api/routes/files.py`

**Files:**
- Modify: `api/routes/files.py`
- Create: `tests/test_summary_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_summary_route.py
"""Tests for GET /files/{stem}/summary."""
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
import pytest

import api.routes.files as files_module


def test_get_summary_returns_404_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(files_module, "OUTPUT_DIR", Path(tmpdir)):
            try:
                files_module.get_summary("nostem")
                assert False, "should have raised HTTPException"
            except HTTPException as e:
                assert e.status_code == 404


def test_get_summary_returns_content():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "lesson01_summary.md").write_text("## 摘要\n\n內容在此。", encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            result = files_module.get_summary("lesson01")
    assert "摘要" in result
    assert "內容在此" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_summary_route.py -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'api.routes.files' has no attribute 'get_summary'`

- [ ] **Step 3: Add `get_summary` to `api/routes/files.py`**

Add after the `get_srt` function (after line 42), before the segments endpoint:

```python
# ── Summary text ──────────────────────────────────────────────
@router.get("/{stem}/summary", response_class=PlainTextResponse)
def get_summary(stem: str):
    path = OUTPUT_DIR / f"{stem}_summary.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")
    return path.read_text(encoding="utf-8", errors="replace")
```

The `PlainTextResponse` import is already present at the top of `api/routes/files.py`.

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_summary_route.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add api/routes/files.py tests/test_summary_route.py
git commit -m "feat(api): add GET /files/{stem}/summary endpoint + tests"
```

---

## Task 4: Register `tasks` router in `api/main.py`

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Update the import line and register the router**

In `api/main.py`, change line 12:
```python
from api.routes import events, files, status, upload
```
to:
```python
from api.routes import events, files, status, tasks, upload
```

And after `app.include_router(status.router)` (line 43), add:
```python
app.include_router(tasks.router)
```

- [ ] **Step 2: Verify the API starts cleanly**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
python -c "from api.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/main.py
git commit -m "feat(api): register tasks router"
```

---

## Task 5: Add CSS for accordion, timeline panel, and save-success

**Files:**
- Modify: `web/static/style.css`

- [ ] **Step 1: Append new CSS at the end of `web/static/style.css`**

```css
/* ── Task accordion (dashboard Recent Completions) ───────── */
details.task-accordion {
  background: var(--bg-card);
  border: 1px solid var(--border);
  transition: border-color var(--transition);
}
details.task-accordion:hover { border-color: var(--border-hi); }
details.task-accordion[open] { border-color: var(--green-dim); }

details.task-accordion > summary {
  display: grid;
  grid-template-columns: auto 1fr auto auto auto;
  align-items: center;
  gap: 1.5rem;
  padding: .8rem 1.2rem;
  list-style: none;
  cursor: pointer;
  user-select: none;
  outline: none;
}
details.task-accordion > summary::-webkit-details-marker { display: none; }

.accordion-chevron {
  font-size: .65rem;
  color: var(--text-dim);
  transition: transform var(--transition);
}
details.task-accordion[open] .accordion-chevron { transform: rotate(90deg); }

.accordion-body {
  border-top: 1px solid var(--border);
  padding: 1rem 1.2rem 1rem 2.5rem;
  background: var(--bg);
}

.accordion-loading {
  font-size: .78rem;
  color: var(--text-dim);
  padding: .5rem 0;
}

.accordion-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}

.accordion-section-label {
  font-size: .65rem;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--green);
  margin-bottom: .45rem;
}

.accordion-summary {
  font-size: .82rem;
  color: var(--text);
  line-height: 1.55;
}

.accordion-transcript { display: flex; flex-direction: column; gap: 1px; }

.accordion-seg {
  display: grid;
  grid-template-columns: 70px 1fr;
  gap: .9rem;
  padding: .3rem .5rem;
  background: var(--bg-card);
  font-size: .8rem;
}

.accordion-link {
  display: inline-block;
  margin-top: .75rem;
  font-size: .75rem;
  color: var(--green);
  text-decoration: none;
  border: 1px solid var(--green-dim);
  padding: .2rem .7rem;
  border-radius: var(--radius);
  transition: background var(--transition);
}
.accordion-link:hover { background: rgba(34,197,94,.08); }

/* ── Timeline panel (SRT viewer + accordion) ─────────────── */
.timeline-panel {
  margin-bottom: 1rem;
  padding: .7rem 1rem;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.timeline-panel-title {
  font-size: .72rem;
  color: var(--text-dim);
  letter-spacing: .08em;
  text-transform: uppercase;
  cursor: pointer;
  user-select: none;
  list-style: none;
  outline: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.timeline-panel-title::-webkit-details-marker { display: none; }
.timeline-panel[open] .timeline-panel-title { margin-bottom: .85rem; }

.tl-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin-bottom: .85rem;
}

.tl-meta-item { display: flex; flex-direction: column; gap: .2rem; }

.tl-meta-label {
  font-size: .62rem;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-dim);
}

.tl-meta-value {
  font-size: .8rem;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.tl-divider {
  height: 1px;
  background: var(--border);
  margin: .75rem 0;
}

.tl-row {
  display: grid;
  grid-template-columns: 90px 1fr 55px;
  align-items: center;
  gap: .75rem;
  margin-bottom: .5rem;
}

.tl-stage {
  font-size: .75rem;
  color: var(--text-dim);
}

.tl-bar-bg {
  height: 4px;
  background: var(--border-hi);
  border-radius: 2px;
  overflow: hidden;
}

.tl-bar { height: 100%; border-radius: 2px; background: var(--green-dim); }
.tl-bar-preprocess  { background: var(--amber-dim); }
.tl-bar-transcribe  { background: var(--blue-dim); }
.tl-bar-summarize   { background: var(--green-dim); }
.tl-bar-diarize     { background: #3a2a5a; }

.tl-dur {
  font-size: .72rem;
  color: var(--text-dim);
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tl-total {
  display: flex;
  justify-content: space-between;
  margin-top: .75rem;
  padding-top: .6rem;
  border-top: 1px solid var(--border);
}

.tl-total-label { font-size: .72rem; color: var(--text-dim); }
.tl-total-value { font-size: .72rem; color: var(--text-mid); font-variant-numeric: tabular-nums; }
.tl-empty { font-size: .78rem; color: var(--text-dim); padding: .3rem 0; }

/* ── Speaker save confirmation ───────────────────────────── */
.speaker-panel-footer {
  margin-top: .6rem;
  display: flex;
  align-items: center;
  gap: .85rem;
}

.save-success {
  font-size: .78rem;
  color: var(--green);
  animation: fade-out 3s ease forwards;
  animation-delay: .5s;
}

@keyframes fade-out {
  0%   { opacity: 1; }
  70%  { opacity: 1; }
  100% { opacity: 0; }
}
```

- [ ] **Step 2: Commit**

```bash
git add web/static/style.css
git commit -m "feat(web): add CSS for accordion, timeline panel, speaker save confirmation"
```

---

## Task 6: Create accordion detail partial + web route

**Files:**
- Create: `web/templates/partials/task_detail.html`
- Modify: `web/main.py`

- [ ] **Step 1: Create `web/templates/partials/task_detail.html`**

```html
{# HTMX partial — expanded accordion body for a completed task #}
<div class="accordion-content">
  <div class="accordion-left">
    <div class="accordion-section-label">摘要</div>
    <div class="accordion-summary">
      {%- if summary -%}
        {{ summary[:220] }}{% if summary | length > 220 %}…{% endif %}
      {%- else -%}
        （無摘要）
      {%- endif -%}
    </div>

    {% if timeline and timeline.stages %}
    <div class="accordion-section-label" style="margin-top: .9rem;">各階段耗時</div>
    {% set dur_values = timeline.stages | selectattr('duration_sec') | map(attribute='duration_sec') | list %}
    {% set max_dur = (dur_values | max) if dur_values else 1 %}
    {% for s in timeline.stages %}
    <div class="tl-row">
      <span class="tl-stage">{{ s.stage }}</span>
      <div class="tl-bar-bg">
        <div class="tl-bar tl-bar-{{ s.stage }}"
             style="width:{{ (((s.duration_sec or 0) / max_dur) * 100) | int }}%"></div>
      </div>
      <span class="tl-dur">
        {%- if s.duration_sec -%}
          {%- if s.duration_sec >= 60 -%}{{ (s.duration_sec // 60) | int }}m {{ (s.duration_sec % 60) | int }}s
          {%- else -%}{{ s.duration_sec }}s{%- endif -%}
        {%- else -%}—{%- endif -%}
      </span>
    </div>
    {% endfor %}
    {% endif %}
  </div>

  <div class="accordion-right">
    <div class="accordion-section-label">逐字稿（前幾段）</div>
    {% if segments %}
    <div class="accordion-transcript">
      {% for seg in segments %}
      <div class="accordion-seg">
        <span class="srt-ts">{{ seg.start[:8] }}</span>
        <span class="srt-text">{{ seg.text }}</span>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="tl-empty">無逐字稿</div>
    {% endif %}
    <a class="accordion-link" href="/srts/{{ stem }}">→ 開啟完整逐字稿</a>
  </div>
</div>
```

- [ ] **Step 2: Add `_get_text` helper and `/partial/task-detail/{stem}` route to `web/main.py`**

Add `_get_text` helper after the existing `_post_json` function (after line 30):

```python
async def _get_text(path: str) -> "str | None":
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{API_URL}{path}")
            if r.status_code == 200:
                return r.text
            return None
    except Exception:
        return None
```

Add the new route after the `status_partial` route (after line 58):

```python
@app.get("/partial/task-detail/{stem}", response_class=HTMLResponse)
async def task_detail_partial(request: Request, stem: str):
    timeline, summary_text, segments = await asyncio.gather(
        _get(f"/tasks/{stem}/timeline"),
        _get_text(f"/files/{stem}/summary"),
        _get(f"/files/{stem}/segments"),
    )
    segments = segments if isinstance(segments, list) else []
    return templates.TemplateResponse(
        request=request,
        name="partials/task_detail.html",
        context={
            "stem": stem,
            "timeline": timeline if isinstance(timeline, dict) else None,
            "summary": summary_text,
            "segments": segments[:3],
        },
    )
```

- [ ] **Step 3: Verify template renders without error**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('web/templates'))
t = env.get_template('partials/task_detail.html')
html = t.render(stem='test', timeline=None, summary=None, segments=[])
print('OK', len(html), 'chars')
"
```

Expected: `OK N chars` (some positive number)

- [ ] **Step 4: Commit**

```bash
git add web/templates/partials/task_detail.html web/main.py
git commit -m "feat(web): add accordion task-detail partial + route"
```

---

## Task 7: Update `status.html` to use accordion on recent completions

**Files:**
- Modify: `web/templates/partials/status.html`

- [ ] **Step 1: Replace the recent completions task rows with `<details>` accordions**

In `web/templates/partials/status.html`, replace the entire `{% for t in recent %}` block (lines 86–97) with:

```html
    {% for t in recent %}
    <details class="task-accordion"
             hx-get="/partial/task-detail/{{ t.stem }}"
             hx-trigger="toggle once"
             hx-target="find .accordion-body"
             hx-swap="innerHTML">
      <summary>
        <span class="dot dot-completed"></span>
        <span class="task-stem">{{ t.stem }}</span>
        <span class="task-stage"><span class="stage-label">done</span></span>
        <span class="task-time">
          {% if t.duration_sec %}{{ "%.0f"|format(t.duration_sec // 60) }}m{% endif %}
        </span>
        <span class="accordion-chevron">▶</span>
      </summary>
      <div class="accordion-body">
        <div class="accordion-loading">loading…</div>
      </div>
    </details>
```

- [ ] **Step 2: Commit**

```bash
git add web/templates/partials/status.html
git commit -m "feat(web): accordion on dashboard recent completions"
```

---

## Task 8: Timeline panel in SRT viewer

**Files:**
- Create: `web/templates/partials/timeline.html`
- Modify: `web/main.py`
- Modify: `web/templates/srt_viewer.html`

- [ ] **Step 1: Add `datetimeformat` Jinja2 filter to `web/main.py`**

Add this import at the top of `web/main.py` (after `from fastapi.templating import Jinja2Templates`):

```python
from datetime import datetime
```

Add this filter registration after the `templates = Jinja2Templates(...)` line:

```python
def _datetimeformat(value):
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)

templates.env.filters["datetimeformat"] = _datetimeformat
```

- [ ] **Step 2: Add `/partial/timeline/{stem}` route to `web/main.py`**

Add this route after the `task_detail_partial` route:

```python
@app.get("/partial/timeline/{stem}", response_class=HTMLResponse)
async def timeline_partial(request: Request, stem: str):
    timeline = await _get(f"/tasks/{stem}/timeline")
    return templates.TemplateResponse(
        request=request,
        name="partials/timeline.html",
        context={"timeline": timeline if isinstance(timeline, dict) else None},
    )
```

- [ ] **Step 3: Create `web/templates/partials/timeline.html`**

```html
{# HTMX partial — processing timeline for SRT viewer #}
{% if timeline %}
<details class="timeline-panel">
  <summary class="timeline-panel-title">
    處理時間軸
    <span style="font-size:.65rem;">▼</span>
  </summary>
  <div class="tl-meta">
    {% if timeline.submitted_at %}
    <div class="tl-meta-item">
      <span class="tl-meta-label">提交時間</span>
      <span class="tl-meta-value">{{ timeline.submitted_at | datetimeformat }}</span>
    </div>
    {% endif %}
    {% if timeline.started_at %}
    <div class="tl-meta-item">
      <span class="tl-meta-label">開始處理</span>
      <span class="tl-meta-value">{{ timeline.started_at | datetimeformat }}</span>
    </div>
    {% endif %}
    {% if timeline.completed_at %}
    <div class="tl-meta-item">
      <span class="tl-meta-label">完成時間</span>
      <span class="tl-meta-value">{{ timeline.completed_at | datetimeformat }}</span>
    </div>
    {% endif %}
    {% if timeline.filename %}
    <div class="tl-meta-item">
      <span class="tl-meta-label">檔案名稱</span>
      <span class="tl-meta-value">{{ timeline.filename }}</span>
    </div>
    {% endif %}
  </div>

  {% if timeline.stages %}
  <div class="tl-divider"></div>
  {% set dur_values = timeline.stages | selectattr('duration_sec') | map(attribute='duration_sec') | list %}
  {% set max_dur = (dur_values | max) if dur_values else 1 %}
  {% for s in timeline.stages %}
  <div class="tl-row">
    <span class="tl-stage">{{ s.stage }}</span>
    <div class="tl-bar-bg">
      <div class="tl-bar tl-bar-{{ s.stage }}"
           style="width:{{ (((s.duration_sec or 0) / max_dur) * 100) | int }}%"></div>
    </div>
    <span class="tl-dur">
      {%- if s.duration_sec -%}
        {%- if s.duration_sec >= 60 -%}{{ (s.duration_sec // 60) | int }}m {{ (s.duration_sec % 60) | int }}s
        {%- else -%}{{ s.duration_sec }}s{%- endif -%}
      {%- else -%}—{%- endif -%}
    </span>
  </div>
  {% endfor %}
  <div class="tl-total">
    <span class="tl-total-label">處理時間</span>
    <span class="tl-total-value">
      {%- if timeline.total_pipeline_sec -%}
        {%- set p = timeline.total_pipeline_sec -%}
        {{ (p // 60) | int }}m {{ (p % 60) | int }}s
      {%- endif -%}
      {%- if timeline.total_wall_sec -%}
        （含等待 {{ (timeline.total_wall_sec // 60) | int }}m）
      {%- endif -%}
    </span>
  </div>
  {% endif %}
</details>
{% endif %}
```

- [ ] **Step 4: Add timeline region to `srt_viewer.html`**

In `web/templates/srt_viewer.html`, insert a new `<div>` after the speaker panel block (after the `{% endif %}` on line 67) and before the `{# ── Transcript ── #}` comment:

```html
    {# ── Timeline panel ───────────────────────────────────── #}
    <div id="timeline-region"
         hx-get="/partial/timeline/{{ stem }}"
         hx-trigger="load"
         hx-swap="innerHTML">
    </div>
```

- [ ] **Step 5: Verify templates render without error**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
python -c "
from jinja2 import Environment, FileSystemLoader, Undefined
env = Environment(loader=FileSystemLoader('web/templates'))
env.filters['datetimeformat'] = lambda v: str(v) if v else '—'
t = env.get_template('partials/timeline.html')
html = t.render(timeline=None)
print('None case OK:', len(html), 'chars')
data = {'submitted_at': 1749470581.0, 'started_at': 1749470583.0, 'completed_at': 1749470820.0,
        'filename': 'test.m4a', 'total_pipeline_sec': 215, 'total_wall_sec': 239,
        'stages': [{'stage':'preprocess','duration_sec':23,'completed_at':1749470606.0},
                   {'stage':'transcribe','duration_sec':192,'completed_at':1749470798.0}]}
html2 = t.render(timeline=data)
print('With data OK:', len(html2), 'chars')
"
```

Expected: two `OK` lines with positive char counts.

- [ ] **Step 6: Commit**

```bash
git add web/main.py web/templates/partials/timeline.html web/templates/srt_viewer.html
git commit -m "feat(web): timeline panel in SRT viewer + datetimeformat filter"
```

---

## Task 9: Speaker save OOB confirmation

**Files:**
- Create: `web/templates/partials/speaker_save_result.html`
- Modify: `web/templates/srt_viewer.html`
- Modify: `web/main.py`

- [ ] **Step 1: Create `web/templates/partials/speaker_save_result.html`**

```html
{# HTMX response for speaker save:
   - main content replaces #transcript (srt_rows content)
   - OOB span updates #save-status indicator #}
{% include "partials/srt_rows.html" %}

<span id="save-status" hx-swap-oob="true" class="save-success">✓ 已儲存</span>
```

- [ ] **Step 2: Update `srt_viewer.html` speaker panel footer**

In `web/templates/srt_viewer.html`, replace the speaker panel footer block (lines 59–64):

```html
        <div class="speaker-panel-footer">
          <button class="speaker-save-btn" type="submit">
            儲存
            <span class="htmx-indicator"> …</span>
          </button>
        </div>
```

with:

```html
        <div class="speaker-panel-footer">
          <button class="speaker-save-btn" type="submit">
            儲存
            <span class="htmx-indicator"> …</span>
          </button>
          <span id="save-status"></span>
        </div>
```

- [ ] **Step 3: Update `save_speaker_names` in `web/main.py`** to render the new template

Change the return statement in the `save_speaker_names` function (currently at the end of the function, returning `partials/srt_rows.html`):

```python
    return templates.TemplateResponse(
        request=request,
        name="partials/speaker_save_result.html",
        context={"segments": segments, "q": "", "total": len(segments), "stem": stem},
    )
```

- [ ] **Step 4: Verify the template renders without error**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('web/templates'))
t = env.get_template('partials/speaker_save_result.html')
html = t.render(segments=[], q='', total=0, stem='test')
print('OK, OOB present:', 'save-status' in html and 'hx-swap-oob' in html)
"
```

Expected: `OK, OOB present: True`

- [ ] **Step 5: Commit**

```bash
git add web/templates/partials/speaker_save_result.html web/templates/srt_viewer.html web/main.py
git commit -m "feat(web): speaker save OOB confirmation (#save-status)"
```

---

## Final Verification

- [ ] **Run the full test suite**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
pytest tests/ -v 2>&1 | tail -20
```

Expected: all previously passing tests still pass, plus new tests for timeline and summary routes pass. Zero failures.

- [ ] **Smoke test: start services and verify UI**

```bash
# In separate terminals or background:
# docker compose up -d  (or podman compose up -d)
# bash scripts/start-pipeline.sh

# Then open:
# http://localhost:3000            — dashboard, click a completed task row
# http://localhost:3000/srts/<stem>  — SRT viewer, check timeline panel appears
```

---

## Self-Review

**Spec coverage check:**
- ✅ Dashboard inline content view (accordion on recent completions) → Tasks 6, 7
- ✅ Time tracking in dashboard accordion → Tasks 2, 6
- ✅ Time tracking in SRT viewer → Tasks 2, 8
- ✅ `GET /tasks/{stem}/timeline` JSON API → Tasks 1, 2, 4
- ✅ Speaker save visual confirmation (OOB) → Task 9
- ✅ `GET /files/{stem}/summary` API → Tasks 3, 4

**Placeholder scan:** All steps have complete code. No TBDs.

**Type consistency:**
- `timeline.stages` is always a list of `{stage, completed_at, duration_sec}` dicts — consistent across task_detail.html and timeline.html
- `_get_text` returns `str | None` — consumed correctly in `task_detail_partial`
- `get_stage_events` returns `list[dict]` with keys `stage`, `ts` — consumed correctly in `get_timeline`
- CSS class `tl-bar-{stage}` names match stage names from the events table (`preprocess`, `transcribe`, `summarize`, `diarize`)
