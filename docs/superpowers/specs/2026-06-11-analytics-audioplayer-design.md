# Analytics Panel + Audio Player Design

**Date:** 2026-06-11
**Goal:** Add a stats panel to the dashboard (speaker time, keyword trends, aggregate counts) and an audio player to the SRT viewer (click-to-seek, auto-scroll highlight). Both features share the diarization data layer and can be built in one implementation cycle.

---

## 1. Architecture

### New files
| File | Purpose |
|------|---------|
| `api/routes/stats.py` | Two endpoints: `/stats/overview` and `/stats/keywords` |
| `web/templates/partials/stats.html` | Stats panel HTML fragment |

### Modified files
| File | Change |
|------|--------|
| `api/main.py` | Register `stats` router |
| `api/srt.py` | Add `to_seconds(ts: str) -> float` helper |
| `api/routes/files.py` | Add `GET /files/{stem}/audio` |
| `web/main.py` | Add `GET /partial/stats` route |
| `web/templates/dashboard.html` | Add stats region (hx-trigger="load") |
| `web/templates/srt_viewer.html` | Add audio player bar + inline JS |
| `web/templates/partials/srt_rows.html` | Add `data-start` attribute to each segment div |

**Nothing changes in:** pipeline/, Redis consumer, DB schema, Docker services.

---

## 2. API Endpoints

### `GET /stats/overview`
Aggregates from two sources — no new DB columns needed.

**Tasks table query:**
```sql
SELECT
  COUNT(*) AS total_tasks,
  SUM(duration_sec) AS total_duration_sec,
  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS success_rate
FROM tasks
```

**Speaker time:** scan all `workspace/3_output/*_diarization.json`, accumulate `(end - start)` per speaker label across all files.

**Response:**
```json
{
  "total_tasks": 142,
  "total_duration_sec": 170580,
  "success_rate": 0.986,
  "speakers": [
    {"label": "SPEAKER_01", "seconds": 82000, "pct": 0.48},
    {"label": "SPEAKER_02", "seconds": 52000, "pct": 0.30}
  ]
}
```

### `GET /stats/keywords`
Scan all `workspace/3_output/*_summary.json`, collect `topic_segments[].topic` across all files, count frequency, return top 10.

**Response:**
```json
[
  {"topic": "機器學習", "count": 14},
  {"topic": "反向傳播", "count": 9}
]
```

### `GET /files/{stem}/audio`
Serve `workspace/2_processing/{stem}_clean.wav` via FastAPI `FileResponse`. If not found, 404. FastAPI `FileResponse` handles HTTP Range headers automatically — browser `<audio>` seek works without extra code.

```python
@router.get("/{stem}/audio")
def get_audio(stem: str):
    path = PROCESSING_DIR / f"{stem}_clean.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/wav")
```

`PROCESSING_DIR = WORKSPACE / "2_processing"` — add alongside existing `OUTPUT_DIR` in `files.py`.

---

## 3. Dashboard Stats Panel

### `web/templates/partials/stats.html`
New partial, loaded once via HTMX on page load. Calls web's own `/partial/stats` route which calls `api/stats/overview` and `api/stats/keywords`.

Structure:
- Row of 4 stat cards: total tasks, total duration (formatted as `Xh Ym`), success rate, speaker count
- Speaker time bar: CSS flexbox, each speaker gets proportional width coloured by index
- Keywords: inline tag list, font-size scaled by count (top word = 100%, bottom word ≈ 70%)

### Dashboard template change
Add above the existing `status-region`:
```html
<div id="stats-region"
     hx-get="/partial/stats"
     hx-trigger="load"
     hx-swap="innerHTML">
</div>
```

`hx-trigger="load"` fires once on page open — no polling. Stats don't need to be real-time.

### Web route `GET /partial/stats`
In `web/main.py`. Calls `api/stats/overview` and `api/stats/keywords` via existing `httpx` client, renders `partials/stats.html`.

---

## 4. SRT Viewer Audio Player

### `api/srt.py` — add `to_seconds`
```python
def to_seconds(ts: str) -> float:
    """Convert SRT timestamp "HH:MM:SS,mmm" to float seconds."""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)
```

### `api/routes/files.py` — expose `start_seconds` in segments endpoint
The existing `GET /files/{stem}/segments` already returns `start` as a string. Add `start_seconds: float` to the response using `to_seconds(s.start)`. The SRT viewer web route passes segments to the template.

### `web/templates/partials/srt_rows.html` — add `data-start`
```html
<div class="srt-seg {% if q %}srt-match{% endif %}"
     data-start="{{ seg.start_seconds }}">
  <span class="srt-ts">{{ seg.start[:8] }}</span>
  <span class="srt-text">{{ seg.text | safe }}</span>
</div>
```

`start_seconds` must be passed through from the web route context — the web `srt_viewer` route already calls `api/files/{stem}/segments` and passes results to the template.

### `web/templates/srt_viewer.html` — audio player bar + JS

Add after `<header>`, before `viewer-header`:
```html
{% if has_audio %}
<div class="audio-bar" id="audio-bar">
  <button class="audio-play-btn" id="play-btn" onclick="togglePlay()">▶</button>
  <div class="audio-seek-track" id="seek-track" onclick="seekTo(event)">
    <div class="audio-seek-fill" id="seek-fill"></div>
    <div class="audio-seek-thumb" id="seek-thumb"></div>
  </div>
  <span class="audio-time" id="audio-time">0:00 / 0:00</span>
  <audio id="player" src="/files/{{ stem }}/audio" preload="metadata"></audio>
</div>
{% endif %}
```

`has_audio` is set in the template context from `speaker_data["has_audio"]` — the existing `GET /files/{stem}/speaker-names` endpoint (already called by the viewer route) is extended to also return `has_audio: bool` by checking whether `workspace/2_processing/{stem}_clean.wav` exists. Web container has no filesystem access, so the check must happen in the API.

**Inline JS** (at bottom of `srt_viewer.html`):
```javascript
const player = document.getElementById('player');
const playBtn = document.getElementById('play-btn');
const seekFill = document.getElementById('seek-fill');
const seekThumb = document.getElementById('seek-thumb');
const audioTime = document.getElementById('audio-time');

function togglePlay() {
  player.paused ? player.play() : player.pause();
}
player.addEventListener('play', () => playBtn.textContent = '⏸');
player.addEventListener('pause', () => playBtn.textContent = '▶');

function fmt(s) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

player.addEventListener('timeupdate', () => {
  const pct = player.duration ? (player.currentTime / player.duration) * 100 : 0;
  seekFill.style.width = pct + '%';
  seekThumb.style.left = pct + '%';
  audioTime.textContent = `${fmt(player.currentTime)} / ${fmt(player.duration || 0)}`;

  // highlight current segment
  const t = player.currentTime;
  let active = null;
  document.querySelectorAll('.srt-seg[data-start]').forEach(el => {
    el.classList.remove('srt-playing');
    const start = parseFloat(el.dataset.start);
    const next = el.nextElementSibling;
    const end = next ? parseFloat(next.dataset.start) : Infinity;
    if (t >= start && t < end) active = el;
  });
  if (active) {
    active.classList.add('srt-playing');
    active.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
});

function seekTo(e) {
  const rect = document.getElementById('seek-track').getBoundingClientRect();
  player.currentTime = ((e.clientX - rect.left) / rect.width) * player.duration;
}

// click segment to seek
document.querySelectorAll('.srt-seg[data-start]').forEach(el => {
  el.style.cursor = 'pointer';
  el.addEventListener('click', () => {
    player.currentTime = parseFloat(el.dataset.start);
    player.play();
  });
});
```

### CSS additions (`web/static/style.css`)
New classes: `.audio-bar`, `.audio-play-btn`, `.audio-seek-track`, `.audio-seek-fill`, `.audio-seek-thumb`, `.audio-time`, `.srt-playing` (green left border + background tint, matching existing design language).

---

## 5. Error Handling

| Scenario | Behaviour |
|----------|-----------|
| No `_diarization.json` files | `overview` returns empty `speakers: []`, bar not shown |
| No `_summary.json` files | `keywords` returns `[]`, tags section not shown |
| `/files/{stem}/audio` 404 | `has_audio=False` in web route → player bar hidden entirely |
| Stats endpoint unreachable | Web renders stats region as empty (no crash) |

---

## 6. Web Route Changes Summary

`web/main.py` additions:
- `GET /partial/stats` — calls `api/stats/overview` + `api/stats/keywords`, renders `partials/stats.html`
- Update existing SRT viewer route to: check `workspace/2_processing/{stem}_clean.wav` exists → pass `has_audio` bool to template; add `start_seconds` to each segment in context

---

## 7. Scope Boundary

**In scope:** Stats panel, audio player, speaker time bar, keyword tags, seek/highlight JS.

**Out of scope:** Semantic search / RAG, speaker renaming from stats panel, export to external tools, mobile-specific layouts, playback speed control (beyond default).
