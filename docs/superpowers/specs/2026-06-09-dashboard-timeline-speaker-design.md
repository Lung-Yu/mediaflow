# Dashboard Preview · Timeline · Speaker Save — Design Spec
**Date:** 2026-06-09

## Overview

Three linked UI improvements:
1. **Dashboard accordion** — view completed task content inline without leaving the dashboard
2. **Time tracking** — per-stage durations in the dashboard accordion and SRT viewer; JSON API for external analysis
3. **Speaker save feedback** — visual confirmation when speaker names are saved

---

## 1. Dashboard Accordion

### Behaviour
Each row in `Recent Completions` becomes a `<details>` element. Clicking a row:
- Expands inline (no page navigation)
- HTMX-fetches `/partial/task-detail/{stem}` on first open (`hx-trigger="toggle once"`)
- Shows a loading placeholder until data arrives
- Content is cached in the DOM; subsequent opens don't re-fetch

### Expanded content
Three columns:
| Left | Right |
|------|-------|
| Summary (first 200 chars of `_summary.md`) | First 3 transcript segments |
| Stage timeline (bar chart) | Link → full SRT viewer |

### New web route
```
GET /partial/task-detail/{stem}   (web/main.py)
```
Calls three API endpoints in parallel:
- `GET /tasks/{stem}/timeline`  — timing data (new, see §2)
- `GET /files/{stem}/summary`   — summary text (new, see §2)
- `GET /files/{stem}/segments`  — first 3 segments

Returns: `partials/task_detail.html` (new template)

### Template changes
- `partials/status.html`: wrap each `t in recent` row in `<details hx-get=... hx-trigger="toggle once">`
- New: `web/templates/partials/task_detail.html`

---

## 2. Time Tracking

### New API endpoints (api/routes/tasks.py — new file)

#### `GET /tasks/{stem}/timeline`
Reads `tasks` table + `events` table. Returns:
```json
{
  "stem": "lesson01",
  "filename": "lesson01.m4a",
  "submitted_at": 1749470581.0,
  "started_at": 1749470583.0,
  "completed_at": 1749470820.0,
  "total_pipeline_sec": 237,
  "total_wall_sec": 2838,
  "stages": [
    {"stage": "preprocess", "completed_at": 1749470606.0, "duration_sec": 23},
    {"stage": "transcribe", "completed_at": 1749470798.0, "duration_sec": 192},
    {"stage": "summarize",  "completed_at": 1749470820.0, "duration_sec": 22}
  ]
}
```

Stage duration computed as: `completed_at(stage[n]) − completed_at(stage[n-1])`, with `started_at` as the zero point for the first stage. Only `stage.completed` events are used.

`total_pipeline_sec` = sum of stage durations (active processing only).
`total_wall_sec` = `completed_at − submitted_at` (includes queue wait).

Returns 404 if stem not found.

#### `GET /files/{stem}/summary`
Reads `workspace/3_output/{stem}_summary.md`. Returns plain text.
Returns 404 if file not found.

### SRT viewer — Timeline panel
New `<details class="timeline-panel">` inserted between the speaker panel and `#transcript` in `srt_viewer.html`. Data fetched on page load via:
```
GET /partial/timeline/{stem}   (web/main.py, new route)
```
which calls `/tasks/{stem}/timeline` and renders `partials/timeline.html` (new).

Panel shows:
- Metadata row: submitted / started / completed timestamps, filename
- Bar chart rows: one per stage, proportional width, stage name + duration + offset from start
- Total row: pipeline time vs wall time

---

## 3. Speaker Save Feedback

### Problem
`save_speaker_names` in `web/main.py` replaces `#transcript` with updated rows. There is no confirmation that the save happened, and the speaker panel itself doesn't update.

### Solution — HTMX OOB swap
1. Change the form target in `srt_viewer.html`:
   - Keep `hx-target="#transcript"` and `hx-swap="innerHTML"` on the form
2. Add a `<span id="save-status">` to the speaker panel footer (initially empty)
3. `save_speaker_names` in `web/main.py` returns the transcript partial **plus** an OOB element:
   ```html
   <!-- main swap → #transcript -->
   <div class="srt-meta">…</div><div class="srt-body">…</div>

   <!-- OOB swap → #save-status -->
   <span id="save-status" hx-swap-oob="true" class="save-success">✓ 已儲存</span>
   ```
4. The success span fades out after 3 s via a CSS animation (`@keyframes fade-out`).

No new routes needed. `web/main.py:save_speaker_names` renders a new partial `partials/speaker_save_result.html` instead of reusing `srt_rows.html` directly.

---

## File Inventory

| File | Change |
|------|--------|
| `api/routes/tasks.py` | **New** — `GET /tasks/{stem}/timeline` |
| `api/routes/files.py` | Add `GET /files/{stem}/summary` |
| `api/main.py` | Register new `tasks` router |
| `web/main.py` | Add `/partial/task-detail/{stem}`, `/partial/timeline/{stem}`; update `save_speaker_names` |
| `web/templates/partials/status.html` | Wrap recent rows in `<details>` with HTMX fetch |
| `web/templates/partials/task_detail.html` | **New** — expanded accordion content |
| `web/templates/partials/timeline.html` | **New** — stage timing panel |
| `web/templates/partials/speaker_save_result.html` | **New** — transcript rows + OOB save confirmation |
| `web/templates/srt_viewer.html` | Add timeline panel include + `#save-status` span |

---

## Data Flow

```
Dashboard accordion click
  → HTMX GET /partial/task-detail/{stem}   (web)
    → parallel: GET /tasks/{stem}/timeline  (api)
                GET /files/{stem}/summary   (api)
                GET /files/{stem}/segments  (api, limit 3)
  ← partials/task_detail.html

SRT viewer load
  → GET /srts/{stem}  (web, existing)
    → adds: GET /partial/timeline/{stem}  (HTMX on page load)
      → GET /tasks/{stem}/timeline  (api)
  ← partials/timeline.html → #timeline-region

Speaker save
  → HTMX POST /srts/{stem}/speaker-names  (web, existing route, updated)
  ← partials/speaker_save_result.html
      main swap → #transcript
      OOB swap  → #save-status
```

---

## Out of Scope
- Editing summary from dashboard
- Real-time stage timing during active processing (only for completed tasks)
- Pagination of recent completions
