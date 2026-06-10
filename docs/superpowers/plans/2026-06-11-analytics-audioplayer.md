# Analytics Panel + Audio Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an analytics stats panel to the dashboard (aggregate counts, speaker time bar, keyword tags) and an audio player to the SRT viewer (click-to-seek, auto-scroll highlight).

**Architecture:** New `api/routes/stats.py` serves two read-only endpoints by querying the SQLite DB and scanning existing output JSON files — no new DB columns. A new `GET /files/{stem}/audio` serves the WAV from `2_processing/`. The dashboard loads stats once on page open via HTMX `hx-trigger="load"`. The SRT viewer gets a sticky HTML5 audio bar; a delegated click listener and `timeupdate` handler handle seek and highlight.

**Tech Stack:** FastAPI (sync routes for file scanning), aiosqlite (DB aggregate query), HTMX (stats one-shot load), HTML5 `<audio>` + vanilla JS (player), Jinja2 (partials), pytest + monkeypatch (tests).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `api/srt.py` | Add `to_seconds(ts) -> float` |
| Modify | `api/db.py` | Add `get_task_aggregates()` |
| Modify | `api/routes/files.py` | Add audio endpoint; add `start_seconds` to segments; add `has_audio` to speaker-names |
| Create | `api/routes/stats.py` | `GET /stats/overview` + `GET /stats/keywords` |
| Modify | `api/main.py` | Register stats router |
| Create | `tests/test_srt_helpers.py` | Unit tests for `to_seconds` |
| Create | `tests/test_files_audio.py` | Tests for audio endpoint + `start_seconds` + `has_audio` |
| Create | `tests/test_stats_route.py` | Tests for stats endpoints |
| Modify | `web/main.py` | Add `GET /partial/stats` route |
| Create | `web/templates/partials/stats.html` | Stats panel HTML fragment |
| Modify | `web/templates/dashboard.html` | Add stats region above status-region |
| Modify | `web/templates/partials/srt_rows.html` | Add `data-start` attr to each segment div |
| Modify | `web/templates/srt_viewer.html` | Add audio player bar + inline JS |
| Modify | `web/static/style.css` | Stats panel + audio player CSS |

---

## Task 1: `to_seconds` helper

**Files:**
- Modify: `api/srt.py`
- Create: `tests/test_srt_helpers.py`

- [ ] **Step 1: Create test file**

```python
# tests/test_srt_helpers.py
import api.srt as srtlib


def test_to_seconds_zero():
    assert srtlib.to_seconds("00:00:00,000") == 0.0


def test_to_seconds_minutes():
    assert srtlib.to_seconds("00:01:30,000") == 90.0


def test_to_seconds_hours():
    assert srtlib.to_seconds("01:00:00,000") == 3600.0


def test_to_seconds_milliseconds():
    assert abs(srtlib.to_seconds("00:00:01,500") - 1.5) < 0.001


def test_to_seconds_full():
    # 1h 2m 3.456s
    assert abs(srtlib.to_seconds("01:02:03,456") - 3723.456) < 0.001


def test_to_seconds_dot_separator():
    # Some SRT files use "." instead of ","
    assert srtlib.to_seconds("00:00:02.500") == 2.5
```

- [ ] **Step 2: Run test to verify it fails**

```
cd /Users/tygrus/Desktop/projects/mediaflow && source venv/bin/activate
pytest tests/test_srt_helpers.py -v
```

Expected: `AttributeError: module 'api.srt' has no attribute 'to_seconds'`

- [ ] **Step 3: Add `to_seconds` to `api/srt.py`**

Add after the `highlight` function (line 51):

```python
def to_seconds(ts: str) -> float:
    """Convert SRT timestamp 'HH:MM:SS,mmm' or 'HH:MM:SS.mmm' to float seconds."""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_srt_helpers.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add api/srt.py tests/test_srt_helpers.py
git commit -m "feat(api): add to_seconds helper to srt module"
```

---

## Task 2: Audio API — endpoint, `start_seconds`, `has_audio`

Three additions to `api/routes/files.py` and one addition to `api/db.py`.

**Files:**
- Modify: `api/routes/files.py`
- Create: `tests/test_files_audio.py`

- [ ] **Step 1: Create test file**

```python
# tests/test_files_audio.py
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import api.routes.files as files_module
from fastapi.testclient import TestClient
from fastapi import FastAPI

app = FastAPI()
app.include_router(files_module.router)
client = TestClient(app)


# ── audio endpoint ───────────────────────────────────────────────

def test_audio_returns_404_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(files_module, "PROCESSING_DIR", Path(tmpdir)):
            r = client.get("/files/nostem/audio")
    assert r.status_code == 404


def test_audio_returns_file_when_present():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        wav = tmp / "mystem_clean.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 36)  # minimal fake WAV header
        with patch.object(files_module, "PROCESSING_DIR", tmp):
            r = client.get("/files/mystem/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")


# ── start_seconds in segments ────────────────────────────────────

def test_segments_include_start_seconds():
    srt_content = (
        "1\n00:00:01,000 --> 00:00:03,000\nhello world\n\n"
        "2\n00:01:00,500 --> 00:01:02,000\nfoo bar\n\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "s1.srt").write_text(srt_content, encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            r = client.get("/files/s1/segments")
    assert r.status_code == 200
    segs = r.json()
    assert segs[0]["start_seconds"] == 1.0
    assert abs(segs[1]["start_seconds"] - 60.5) < 0.001


# ── has_audio in speaker-names ───────────────────────────────────

def test_speaker_names_has_audio_false_when_wav_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with (
            patch.object(files_module, "OUTPUT_DIR", Path(tmpdir)),
            patch.object(files_module, "PROCESSING_DIR", Path(tmpdir)),
        ):
            result = files_module.get_speaker_names("nostem")
    assert result["has_audio"] is False


def test_speaker_names_has_audio_true_when_wav_present():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "s1_clean.wav").write_bytes(b"RIFF")
        with (
            patch.object(files_module, "OUTPUT_DIR", tmp),
            patch.object(files_module, "PROCESSING_DIR", tmp),
        ):
            result = files_module.get_speaker_names("s1")
    assert result["has_audio"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_files_audio.py -v
```

Expected: failures on missing `PROCESSING_DIR`, missing audio endpoint, missing `start_seconds`, missing `has_audio`.

- [ ] **Step 3: Update `api/routes/files.py`**

Add `PROCESSING_DIR` constant after the existing `OUTPUT_DIR` line (line 11):

```python
PROCESSING_DIR = WORKSPACE / "2_processing"
```

Add audio endpoint after `get_summary` (after line 51):

```python
# ── Audio file ────────────────────────────────────────────────
@router.get("/{stem}/audio")
def get_audio(stem: str):
    path = PROCESSING_DIR / f"{stem}_clean.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav")
```

Update `get_segments` to include `start_seconds` — replace the return list comprehension (lines 62–70):

```python
    return [
        {
            "index": s.index,
            "start": s.start,
            "end": s.end,
            "start_seconds": srtlib.to_seconds(s.start),
            "text": srtlib.highlight(s.text, q) if q else s.text,
        }
        for s in segments
    ]
```

Update `get_speaker_names` to return `has_audio` — replace the final `return` statement (line 105):

```python
    has_audio = (PROCESSING_DIR / f"{stem}_clean.wav").exists()
    return {"speakers": speakers, "counts": counts, "names": names, "has_audio": has_audio}
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_files_audio.py -v
```

Expected: 5 passed

- [ ] **Step 5: Also run existing speaker-names tests to verify no regression**

```
pytest tests/test_api_speaker_names.py -v
```

Expected: all passed (the new `has_audio` key is additive)

- [ ] **Step 6: Commit**

```bash
git add api/routes/files.py tests/test_files_audio.py
git commit -m "feat(api): add audio endpoint, start_seconds to segments, has_audio to speaker-names"
```

---

## Task 3: Stats API

**Files:**
- Modify: `api/db.py`
- Create: `api/routes/stats.py`
- Modify: `api/main.py`
- Create: `tests/test_stats_route.py`

- [ ] **Step 1: Create test file**

```python
# tests/test_stats_route.py
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

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


def test_get_task_aggregates_empty(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(
        tmp_db.get_task_aggregates()
    )
    assert result["total_tasks"] == 0
    assert result["total_duration_sec"] == 0.0
    assert result["completed"] == 0


def test_get_task_aggregates_counts(tmp_db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(tmp_db.upsert_task(
        "s1", filename="s1.m4a", status="completed", duration_sec=120.0
    ))
    loop.run_until_complete(tmp_db.upsert_task(
        "s2", filename="s2.m4a", status="failed", duration_sec=30.0
    ))
    result = loop.run_until_complete(tmp_db.get_task_aggregates())
    assert result["total_tasks"] == 2
    assert result["total_duration_sec"] == 150.0
    assert result["completed"] == 1


def test_speaker_totals_empty_dir():
    import api.routes.stats as stats_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        result = stats_mod._speaker_totals(Path(tmpdir))
    assert result == []


def test_speaker_totals_aggregates_across_files():
    import api.routes.stats as stats_mod
    diar1 = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0},
        {"speaker": "SPEAKER_01", "start": 10.0, "end": 16.0},
    ]
    diar2 = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "a_diarization.json").write_text(json.dumps(diar1), encoding="utf-8")
        (tmp / "b_diarization.json").write_text(json.dumps(diar2), encoding="utf-8")
        result = stats_mod._speaker_totals(tmp)
    # SPEAKER_00: 10 + 5 = 15s, SPEAKER_01: 6s, total 21s
    assert result[0]["label"] == "SPEAKER_00"
    assert result[0]["seconds"] == 15.0
    assert abs(result[0]["pct"] - 15 / 21) < 0.01
    assert result[1]["label"] == "SPEAKER_01"


def test_keyword_counts_empty_dir():
    import api.routes.stats as stats_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        result = stats_mod._keyword_counts(Path(tmpdir))
    assert result == []


def test_keyword_counts_top_10():
    import api.routes.stats as stats_mod
    summaries = [
        {"topic_segments": [{"topic": "機器學習"}, {"topic": "神經網路"}, {"topic": "機器學習"}]},
        {"topic_segments": [{"topic": "機器學習"}, {"topic": "深度學習"}]},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for i, s in enumerate(summaries):
            (tmp / f"file{i}_summary.json").write_text(json.dumps(s), encoding="utf-8")
        result = stats_mod._keyword_counts(tmp)
    assert result[0] == {"topic": "機器學習", "count": 3}
    assert len(result) <= 10
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_stats_route.py -v
```

Expected: failures on missing `get_task_aggregates`, missing `api.routes.stats`.

- [ ] **Step 3: Add `get_task_aggregates` to `api/db.py`**

Add after the `get_status_overview` function:

```python
async def get_task_aggregates() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(duration_sec), 0), "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) "
            "FROM tasks"
        )
        row = await cur.fetchone()
        return {
            "total_tasks": row[0] or 0,
            "total_duration_sec": float(row[1] or 0),
            "completed": row[2] or 0,
        }
```

- [ ] **Step 4: Create `api/routes/stats.py`**

```python
"""Aggregate statistics over all pipeline tasks and output files."""
import json
import os
from pathlib import Path

from fastapi import APIRouter

from api import db

router = APIRouter(prefix="/stats")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
OUTPUT_DIR = WORKSPACE / "3_output"


def _speaker_totals(output_dir: Path) -> list[dict]:
    totals: dict[str, float] = {}
    for path in output_dir.glob("*_diarization.json"):
        try:
            segs = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for seg in segs:
            sp = seg.get("speaker", "")
            if sp:
                totals[sp] = totals.get(sp, 0.0) + (seg.get("end", 0) - seg.get("start", 0))
    total_all = sum(totals.values()) or 1.0
    return [
        {"label": sp, "seconds": round(secs, 1), "pct": round(secs / total_all, 3)}
        for sp, secs in sorted(totals.items(), key=lambda x: -x[1])
    ]


def _keyword_counts(output_dir: Path) -> list[dict]:
    counts: dict[str, int] = {}
    for path in output_dir.glob("*_summary.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for seg in data.get("topic_segments", []):
            topic = seg.get("topic", "").strip()
            if topic:
                counts[topic] = counts.get(topic, 0) + 1
    return [
        {"topic": t, "count": c}
        for t, c in sorted(counts.items(), key=lambda x: -x[1])[:10]
    ]


@router.get("/overview")
async def get_overview():
    agg = await db.get_task_aggregates()
    total = agg["total_tasks"]
    success_rate = (agg["completed"] / total) if total > 0 else 0.0
    return {
        "total_tasks": total,
        "total_duration_sec": agg["total_duration_sec"],
        "success_rate": round(success_rate, 3),
        "speakers": _speaker_totals(OUTPUT_DIR),
    }


@router.get("/keywords")
def get_keywords():
    return _keyword_counts(OUTPUT_DIR)
```

- [ ] **Step 5: Register stats router in `api/main.py`**

Update the import line (line 16):
```python
from api.routes import events, files, stats, status, tasks, upload
```

Add after `app.include_router(files.router)` (after line 65):
```python
app.include_router(stats.router)
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_stats_route.py -v
```

Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add api/db.py api/routes/stats.py api/main.py tests/test_stats_route.py
git commit -m "feat(api): add stats endpoints — overview and keywords"
```

---

## Task 4: Stats frontend

**Files:**
- Create: `web/templates/partials/stats.html`
- Modify: `web/main.py`
- Modify: `web/templates/dashboard.html`
- Modify: `web/static/style.css`

- [ ] **Step 1: Create `web/templates/partials/stats.html`**

```html
{# Stats panel — loaded once on dashboard open via hx-trigger="load" #}
{% set total = overview.get('total_tasks', 0) %}
{% set dur = overview.get('total_duration_sec', 0) | int %}
{% set rate = overview.get('success_rate', 0) %}
{% set speakers = overview.get('speakers', []) %}

<div class="stats-panel">
  <div class="stats-cards">
    <div class="stats-card">
      <div class="stats-value">{{ total }}</div>
      <div class="stats-label">錄音總數</div>
    </div>
    <div class="stats-card">
      <div class="stats-value" style="color:var(--blue)">{{ dur // 3600 }}h {{ (dur % 3600) // 60 }}m</div>
      <div class="stats-label">總時長</div>
    </div>
    <div class="stats-card">
      <div class="stats-value" style="color:var(--green)">{{ "%.1f"|format(rate * 100) }}%</div>
      <div class="stats-label">成功率</div>
    </div>
    <div class="stats-card">
      <div class="stats-value" style="color:var(--amber)">{{ speakers | length }}</div>
      <div class="stats-label">已識別講者</div>
    </div>
  </div>

  {% if speakers %}
  <div class="stats-speaker-section">
    <div class="stats-section-label">講者發言時間</div>
    <div class="stats-speaker-bar">
      {% for sp in speakers %}
      <div class="stats-speaker-seg stats-color-{{ loop.index0 % 3 }}"
           style="flex:{{ sp.pct }}"
           title="{{ sp.label }} {{ '%.0f'|format(sp.pct * 100) }}%"></div>
      {% endfor %}
    </div>
    <div class="stats-speaker-legend">
      {% for sp in speakers %}
      <span class="stats-speaker-item stats-color-{{ loop.index0 % 3 }}">
        ■ {{ sp.label }} {{ '%.0f'|format(sp.pct * 100) }}%
      </span>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if keywords %}
  <div class="stats-keywords-section">
    <div class="stats-section-label">常見主題</div>
    <div class="stats-keyword-tags">
      {% for kw in keywords %}
      <span class="stats-keyword-tag">{{ kw.topic }} ×{{ kw.count }}</span>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 2: Add `GET /partial/stats` to `web/main.py`**

Add after the `status_partial` function (after line 101), before the `task_detail_partial`:

```python
@app.get("/partial/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    overview, keywords = await asyncio.gather(
        _get("/stats/overview"),
        _get("/stats/keywords"),
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/stats.html",
        context={
            "overview": overview if isinstance(overview, dict) else {},
            "keywords": keywords if isinstance(keywords, list) else [],
        },
    )
```

- [ ] **Step 3: Add stats region to `web/templates/dashboard.html`**

Add before the `status-region` div (before line 26), after the closing `</header>` tag:

```html
    {# Stats panel — loaded once on page open #}
    <div id="stats-region"
         hx-get="/partial/stats"
         hx-trigger="load"
         hx-swap="innerHTML">
    </div>
```

- [ ] **Step 4: Add stats CSS to `web/static/style.css`**

Add at the end of the file:

```css
/* ── Stats panel ──────────────────────────────────────────────── */
.stats-panel {
  padding: .75rem 1.25rem;
  border-bottom: 1px solid var(--bg-card-alt);
  display: flex;
  flex-direction: column;
  gap: .75rem;
}

.stats-cards {
  display: flex;
  gap: .75rem;
}

.stats-card {
  flex: 1;
  background: var(--bg-card);
  border-radius: 6px;
  padding: .75rem 1rem;
  text-align: center;
}

.stats-value {
  font-size: 1.35rem;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.stats-label {
  font-size: .7rem;
  color: var(--text-dim);
  margin-top: .2rem;
  letter-spacing: .03em;
}

.stats-section-label {
  font-size: .7rem;
  color: var(--text-dim);
  margin-bottom: .4rem;
  letter-spacing: .03em;
}

.stats-speaker-bar {
  display: flex;
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
  gap: 1px;
}

.stats-speaker-seg { min-width: 4px; }
.stats-color-0 { background: var(--green); }
.stats-color-1 { background: var(--blue); }
.stats-color-2 { background: var(--amber); }

.stats-speaker-legend {
  display: flex;
  gap: 1rem;
  margin-top: .4rem;
  flex-wrap: wrap;
}

.stats-speaker-item {
  font-size: .72rem;
  color: var(--text-mid);
}

.stats-color-0.stats-speaker-item { color: var(--green); }
.stats-color-1.stats-speaker-item { color: var(--blue); }
.stats-color-2.stats-speaker-item { color: var(--amber); }

.stats-keyword-tags {
  display: flex;
  gap: .4rem;
  flex-wrap: wrap;
}

.stats-keyword-tag {
  background: var(--blue-dim);
  color: var(--blue);
  font-size: .72rem;
  padding: .2rem .55rem;
  border-radius: 10px;
}
```

- [ ] **Step 5: Verify stats panel renders**

Start the stack if not running: `bash scripts/start-services.sh`

Open http://localhost:3000 — the stats panel should appear above the queue with stat cards. If no data, cards show zeros and speaker/keyword sections are hidden (controlled by `{% if speakers %}` and `{% if keywords %}`).

- [ ] **Step 6: Commit**

```bash
git add web/templates/partials/stats.html web/main.py web/templates/dashboard.html web/static/style.css
git commit -m "feat(web): add stats panel to dashboard — counts, speaker bar, keyword tags"
```

---

## Task 5: Audio player frontend

**Files:**
- Modify: `web/templates/partials/srt_rows.html`
- Modify: `web/templates/srt_viewer.html`
- Modify: `web/static/style.css`

- [ ] **Step 1: Add `data-start` attribute to `web/templates/partials/srt_rows.html`**

Replace the entire `.srt-seg` div (the line starting with `<div class="srt-seg`):

```html
<div class="srt-seg {% if q %}srt-match{% endif %}"
     {% if seg.start_seconds is defined %}data-start="{{ seg.start_seconds }}"{% endif %}>
  <span class="srt-ts">{{ seg.start[:8] }}</span>
  <span class="srt-text">{{ seg.text | safe }}</span>
</div>
```

- [ ] **Step 2: Add audio player CSS to `web/static/style.css`**

Append after the stats CSS added in Task 4:

```css
/* ── Audio player bar ─────────────────────────────────────────── */
.audio-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--bg-card);
  border-bottom: 1px solid var(--bg-card-alt);
  padding: .6rem 1.25rem;
  display: flex;
  align-items: center;
  gap: .75rem;
}

.audio-play-btn {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: none;
  background: var(--green);
  color: #000;
  font-size: .8rem;
  cursor: pointer;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.audio-play-btn:hover { opacity: .85; }

.audio-seek-track {
  flex: 1;
  height: 6px;
  background: var(--bg-card-alt);
  border-radius: 3px;
  position: relative;
  cursor: pointer;
}

.audio-seek-fill {
  height: 100%;
  background: var(--green);
  border-radius: 3px;
  width: 0;
  pointer-events: none;
}

.audio-seek-thumb {
  position: absolute;
  top: 50%;
  left: 0;
  width: 12px;
  height: 12px;
  background: var(--text);
  border-radius: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.audio-time {
  font-size: .72rem;
  color: var(--text-mid);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.srt-seg.srt-playing {
  background: var(--green-dim);
  border-left-color: var(--green);
}

.srt-seg[data-start] { cursor: pointer; }
```

- [ ] **Step 3: Add audio player bar and JS to `web/templates/srt_viewer.html`**

After the `<header>` closing tag (after line 19), add the player bar:

```html
    {# ── Audio player (only when WAV is available for this stem) #}
    {% if has_audio %}
    <div class="audio-bar" id="audio-bar">
      <button class="audio-play-btn" id="play-btn" onclick="togglePlay()">▶</button>
      <div class="audio-seek-track" id="seek-track" onclick="seekClick(event)">
        <div class="audio-seek-fill" id="seek-fill"></div>
        <div class="audio-seek-thumb" id="seek-thumb"></div>
      </div>
      <span class="audio-time" id="audio-time">0:00 / 0:00</span>
      <audio id="player" src="/files/{{ stem }}/audio" preload="metadata"></audio>
    </div>
    {% endif %}
```

Before `</body>` (replace or add after the closing `</div>` of `.layout`), add the inline script:

```html
  {% if has_audio %}
  <script>
    (function () {
      const player = document.getElementById('player');
      const playBtn = document.getElementById('play-btn');
      const seekFill = document.getElementById('seek-fill');
      const seekThumb = document.getElementById('seek-thumb');
      const timeLabel = document.getElementById('audio-time');

      function fmt(s) {
        if (!isFinite(s)) return '0:00';
        const m = Math.floor(s / 60);
        return m + ':' + String(Math.floor(s % 60)).padStart(2, '0');
      }

      function togglePlay() {
        player.paused ? player.play() : player.pause();
      }
      window.togglePlay = togglePlay;

      player.addEventListener('play',  () => playBtn.textContent = '⏸');
      player.addEventListener('pause', () => playBtn.textContent = '▶');

      player.addEventListener('timeupdate', function () {
        const pct = player.duration ? (player.currentTime / player.duration) * 100 : 0;
        seekFill.style.width  = pct + '%';
        seekThumb.style.left  = pct + '%';
        timeLabel.textContent = fmt(player.currentTime) + ' / ' + fmt(player.duration);

        const t = player.currentTime;
        const segs = document.querySelectorAll('.srt-seg[data-start]');
        let active = null;
        for (let i = 0; i < segs.length; i++) {
          const start = parseFloat(segs[i].dataset.start);
          const end   = i + 1 < segs.length ? parseFloat(segs[i + 1].dataset.start) : Infinity;
          segs[i].classList.remove('srt-playing');
          if (t >= start && t < end) active = segs[i];
        }
        if (active) {
          active.classList.add('srt-playing');
          active.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      });

      function seekClick(e) {
        if (!player.duration) return;
        const rect = document.getElementById('seek-track').getBoundingClientRect();
        player.currentTime = ((e.clientX - rect.left) / rect.width) * player.duration;
      }
      window.seekClick = seekClick;

      // Use event delegation so clicks work after HTMX search swaps #transcript
      document.getElementById('transcript').addEventListener('click', function (e) {
        const seg = e.target.closest('.srt-seg[data-start]');
        if (!seg) return;
        player.currentTime = parseFloat(seg.dataset.start);
        player.play();
      });
    })();
  </script>
  {% endif %}
```

- [ ] **Step 4: Pass `has_audio` through the web route in `web/main.py`**

Update `srt_viewer` route (line 143) to add `has_audio` to the context. Replace the `return templates.TemplateResponse(...)` block:

```python
    has_audio = bool(speaker_data.get("has_audio", False)) if isinstance(speaker_data, dict) else False
    return templates.TemplateResponse(
        request=request,
        name="srt_viewer.html",
        context={
            "stem": stem,
            "segments": segments,
            "q": q,
            "total": len(segments),
            "speaker_data": speaker_data,
            "has_audio": has_audio,
        },
    )
```

- [ ] **Step 5: Verify audio player works end-to-end**

Open a transcript that has a `_clean.wav` in `workspace/2_processing/`:

```bash
# List stems with WAV files
ls workspace/2_processing/*_clean.wav | sed 's|.*\/||;s|_clean.wav||'
```

Navigate to `http://localhost:3000/srts/<stem>` — the audio bar should appear at the top. Click play, seek by clicking the track, click a subtitle line to jump to that timestamp.

If there's no WAV for a stem, no player bar appears (graceful degradation).

- [ ] **Step 6: Commit**

```bash
git add web/templates/partials/srt_rows.html web/templates/srt_viewer.html web/static/style.css web/main.py
git commit -m "feat(web): add audio player to SRT viewer — click-to-seek, auto-highlight"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ `GET /stats/overview` — Task 3
- ✅ `GET /stats/keywords` — Task 3
- ✅ `GET /files/{stem}/audio` — Task 2
- ✅ `has_audio` via speaker-names extension — Task 2
- ✅ `start_seconds` in segments — Task 2
- ✅ `to_seconds` helper — Task 1
- ✅ Dashboard stats region with `hx-trigger="load"` — Task 4
- ✅ `web/partial/stats` route — Task 4
- ✅ Stats CSS — Task 4
- ✅ `data-start` on srt_rows — Task 5
- ✅ Audio player bar HTML — Task 5
- ✅ `timeupdate` highlight + auto-scroll — Task 5
- ✅ Click-to-seek via event delegation (HTMX-safe) — Task 5
- ✅ `has_audio` passed to srt_viewer template — Task 5
- ✅ Error handling: empty dirs → `[]`, missing WAV → no player bar — Tasks 3 + 5

**Type consistency:**
- `to_seconds` defined in Task 1, used as `srtlib.to_seconds(s.start)` in Task 2 ✅
- `_speaker_totals` and `_keyword_counts` defined and tested in Task 3 ✅
- `has_audio` key added to `get_speaker_names` return dict in Task 2, read as `speaker_data.get("has_audio")` in Task 5 ✅
- `start_seconds` added to segments dict in Task 2, used as `seg.start_seconds` in template Task 5 ✅
