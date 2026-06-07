# mediaflow — Claude Handoff Guide

Audio recording pipeline that converts recordings into transcripts and structured summaries, served via a web dashboard. Built for a Mac mini running Apple Silicon + Docker.

---

## System Architecture

```
[Host — native, Apple Silicon GPU-bound]
  pipeline/watcher.py
    ├── watches workspace/1_input/ (watchdog)
    ├── runs FFmpeg → Whisper → Ollama per file (ThreadPoolExecutor, max_workers=2)
    └── publishes events to Redis Streams after each stage

  External services (must be running before pipeline starts):
    Whisper HTTP service  localhost:9001   (mlx-whisper or compatible)
    Ollama               localhost:11434   (qwen2.5:7b must be pulled)

                    ↕ Redis Streams (mediaflow:events)

[Docker Compose — api + web + redis]
  redis   port 6379   — Stream MQ with appendfsync always
  api     port 8080   — FastAPI: Redis consumer + REST endpoints
  web     port 3000   — Jinja2 + HTMX: dashboard + SRT browser
```

**Why this split**: Whisper (mlx-whisper) and Ollama use Apple Silicon GPU and cannot run inside Docker. The API + Web layer is fully portable and can be deployed remotely.

**Event flow**: pipeline → Redis xadd → api/mq/consumer.py reads via XREADGROUP → writes to pipeline.db (SQLite) → web polls /status/ via HTMX.

---

## Workspace Layout

```
workspace/
  1_input/       ← drop audio/video files here to start processing
  2_processing/  ← FFmpeg WAV intermediates ({stem}_clean.wav)
  3_output/      ← final SRT, _summary.md, _summary.json, processed WAV
  4_archive/     ← original input files after successful pipeline
```

Files that fail are renamed to `{original}.failed` in-place so the watcher skips them on restart.

---

## Key Files

```
pipeline/
  watcher.py          — watchdog loop + startup recovery scan + ThreadPoolExecutor
  stages.py           — preprocess / transcribe / correct_srt / summarize stage runners
  mq/publisher.py     — Redis xadd wrapper (EventPublisher)
  config.py           — load config.yaml + workspace path helper

api/
  main.py             — FastAPI lifespan: init DB, reconcile, start Redis consumer
  event_processor.py  — shared process_event() used by HTTP route + Redis consumer
  mq/consumer.py      — XREADGROUP loop: reads stream → process_event() → xack
  db.py               — aiosqlite: tasks + events tables, upsert_task, get_status_overview
  reconcile.py        — on startup, scan 3_output/*.srt and fill DB gaps
  routes/events.py    — POST /events/stage-complete (HTTP alternative to Redis)
  routes/files.py     — GET /files/ list, /files/{stem}/srt, /files/{stem}/segments
  routes/status.py    — GET /status/ for dashboard data
  srt.py              — SRT parser + segment search + highlight
  webhook.py          — fire-and-forget POST on task.completed / task.failed

web/
  main.py             — FastAPI serving Jinja2 templates, calls api via httpx
  templates/
    dashboard.html    — HTMX live poll (/partial/status every 30s)
    srts.html         — SRT file list
    srt_viewer.html   — transcript viewer with search + highlight

config.yaml           — gitignored; copy from config.yaml.example
docker-compose.yml    — redis + api + web; volume mounts workspace/ and data/
```

---

## Configuration (`config.yaml`)

```yaml
pipeline:
  workspace_dir: ./workspace
  supported_formats: [.mp4, .m4a, .mp3, .wav, .flac]

whisper:
  service_url: http://localhost:9001
  language: zh

ollama:
  service_url: http://localhost:11434
  model: qwen2.5:7b          # must be pulled: ollama pull qwen2.5:7b

redis:
  host: localhost
  port: 6379
  stream_key: mediaflow:events
  consumer_group: api-consumers

api:
  event_url: http://localhost:8080/events/stage-complete

notification:
  webhook_url: ""            # optional: n8n / ntfy / Slack

# Phase 3 options (both default off/auto):
#   pipeline.llm_correction: true   — Ollama correction pass after Whisper (~30s extra)
#   pipeline.recording_type: course — use course-specific prompts (auto-detects from stem)
```

---

## External Service APIs

### Whisper (`pipeline/stages.py: transcribe()`)

```python
# POST /transcribe_segments
httpx.post(
    "http://localhost:9001/transcribe_segments",
    files={"audio": (audio_path.name, file_handle)},
    params={"language": "zh"},
    timeout=1800.0,
)
# Response: {"segments": [{"id", "start", "end", "text", "avg_logprob", "no_speech_prob"}]}
```

Also available on port 9001: `/transcribe` (plain text, whisper-medium) and `/transcribe_large` (whisper-large-v3). These are used for segment-level verification (not yet implemented in mediaflow).

### Ollama (`pipeline/stages.py: summarize()`)

```python
import ollama
resp = ollama.chat(
    model="qwen2.5:7b",
    messages=[{"role": "user", "content": prompt}],
)
text = resp["message"]["content"]
```

### FFmpeg (`pipeline/stages.py: preprocess()`)

9-stage speech-enhancement filter chain:
```
aformat=channel_layouts=mono:sample_rates=16000,
highpass=f=80,
afftdn=nf=-25,
anlmdn=s=7:p=0.002:r=0.002:m=15,
speechnorm=e=12.5:r=0.00001:l=1,
equalizer=f=1500:width_type=o:width=2:g=3,
loudnorm=I=-16:TP=-1.5:LRA=11,
dynaudnorm=f=200:g=11:p=0.95:m=5.0,
silenceremove=start_periods=1:start_silence=0.5:start_threshold=-50dB:detection=peak
```
Output: 16kHz mono WAV.

---

## Redis Streams Schema

Stream key: `mediaflow:events`  
Consumer group: `api-consumers`

Each message is a flat dict (all values are strings — Redis requirement):

```python
# Pipeline publishes these event types:
{"event": "task.submitted",  "stem": "lesson01", "filename": "lesson01.m4a", "ts": "1234567890.0"}
{"event": "stage.completed", "stem": "lesson01", "stage": "preprocessing",  "ts": "..."}
{"event": "stage.completed", "stem": "lesson01", "stage": "transcription",  "output_path": "/path/to/lesson01.srt", "ts": "..."}
{"event": "stage.completed", "stem": "lesson01", "stage": "summary",        "ts": "..."}
{"event": "task.completed",  "stem": "lesson01", "output_path": "/path/to/lesson01.srt", "ts": "..."}
{"event": "task.failed",     "stem": "lesson01", "error_msg": "...",         "ts": "..."}
```

Status mapping in `api/event_processor.py`:
```python
"task.submitted"  → "submitted"
"stage.completed" → "processing"
"task.completed"  → "completed"
"task.failed"     → "failed"
```

---

## Database Schema (`data/pipeline.db`)

```sql
CREATE TABLE tasks (
    stem            TEXT PRIMARY KEY,
    filename        TEXT,
    status          TEXT NOT NULL DEFAULT 'submitted',
    current_stage   TEXT,
    submitted_at    REAL,
    started_at      REAL,    -- set when preprocessing stage completes
    completed_at    REAL,
    duration_sec    REAL,
    error_msg       TEXT,
    output_srt_path TEXT
);

CREATE TABLE events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    stem    TEXT NOT NULL,
    event   TEXT NOT NULL,
    stage   TEXT,
    status  TEXT,
    ts      REAL,
    payload TEXT           -- JSON of full event dict
);
```

---

## Implementation Status

### ✅ Done

| Phase | Item | File |
|-------|------|------|
| P0-1 | Config centralisation | `config.yaml` + `pipeline/config.py` |
| P0-2 | Docker Compose (api + web + redis) | `docker-compose.yml` |
| P0-3 | Pipeline → Redis → API event bridge | `pipeline/mq/publisher.py`, `api/mq/consumer.py` |
| P0-4 | OS-specific replacement (xattr, launchd, osascript) | watcher + webhook |
| P1-1 | SQLite state table | `api/db.py` |
| P1-2 | Startup file scan + API reconcile | `pipeline/watcher.py`, `api/reconcile.py` |
| P1-3 | Error isolation (.failed suffix) | `pipeline/watcher.py` |
| P2-1 | Dashboard (HTMX live poll) | `web/templates/dashboard.html` |
| P2-2 | SRT browser + full-text search | `web/templates/srts.html`, `srt_viewer.html` |
| P2-3 | Webhook notification on completion | `api/webhook.py` |
| —    | Full pipeline stages | `pipeline/stages.py` |
| P1-4 | Stage incremental re-run (`--from-stage`) | `pipeline/rerun.py` |
| P5   | Smoke test + fixture audio | `tests/fixtures/test-speech.m4a`, `tests/run-pipeline-test.sh` |
| P3   | Recording-type prompts + LLM correction | `pipeline/stages.py` (`correct_srt`, `_detect_recording_type`) |

### ❌ Not Yet Implemented

**Phase 4 — Domain-specific features**

- **Segment verification**: re-transcribe suspicious segments (low `avg_logprob`, high `no_speech_prob`) with whisper-large-v3 for cross-validation. Reference: `automate/pipeline/modules/verifier.py`.
- **Speaker diarization**: pyannote.audio (needs HuggingFace token). Reference: `automate/pipeline/modules/diarizer.py`.
- **Chapter detection**: insert chapter markers based on silence gaps and semantic topic boundaries.

**Phase 5 — Remaining**

- Mermaid architecture diagram in README

---

## How to Run (Development)

```bash
# 1. Copy and edit config
cp config.yaml.example config.yaml

# 2. Start Docker services (Redis + API + Web)
bash scripts/start-services.sh
# Web: http://localhost:3000   API: http://localhost:8080

# 3. Start external services (must be running on host)
#    Whisper: whatever service listens on localhost:9001/transcribe_segments
#    Ollama:  ollama serve  (and: ollama pull qwen2.5:7b)

# 4. Start pipeline watcher
bash scripts/start-pipeline.sh

# 5. Drop a file to test
cp some_recording.m4a workspace/1_input/
```

### Python Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Always activate venv before running any Python in this project.

### Container Runtime: Docker or Podman

Both `docker compose` and `podman compose` work with the same `docker-compose.yml`.

**Docker (default)**
```bash
# Comes with Docker Desktop. No extra setup.
docker compose up -d
docker compose logs -f api
docker compose down
```

**Podman**
```bash
# Install
brew install podman podman-compose

# One-time machine init (rootless VM on macOS)
podman machine init
podman machine start

# Use podman-compose as a drop-in replacement
podman-compose up -d
podman-compose logs -f api
podman-compose down

# Or use podman's built-in compose (>= Podman 4.7)
podman compose up -d
```

Key differences to be aware of:
- Podman is rootless by default — volume permissions are handled differently. If the `api` container can't write to `./data/`, check that the host `data/` dir is writable by the current user (uid mapping).
- `podman-compose` does not support all Docker Compose v3 features; this project only uses `build`, `volumes`, `ports`, `depends_on`, `healthcheck` — all supported.
- Socket path differs: if anything in the project tries to talk to the Docker socket directly, point it at `$XDG_RUNTIME_DIR/podman/podman.sock` instead.

`scripts/start-services.sh` calls `docker compose up -d` — change to `podman compose up -d` if running under Podman.

### Smoke Test (End-to-End Verification)

A 25-second Chinese speech fixture is included for testing:

```
tests/
  fixtures/test-speech.m4a   — macOS TTS (Meijia voice), 25s, 316KB
  run-pipeline-test.sh       — drops the file, polls for completion, checks outputs
```

**Expected content** (what Whisper should transcribe):
> 這是一段測試音訊，用來驗證 mediaflow 的轉錄與摘要功能。本系統使用 Whisper 進行語音辨識，再由 Ollama 生成摘要。測試包含三個階段：音訊前處理、語音轉文字、以及摘要生成。如果你看到這段文字出現在 SRT 檔案中，代表整條 pipeline 運作正常。

**Run the test:**
```bash
# Prerequisites: services + pipeline watcher must already be running
bash tests/run-pipeline-test.sh
```

Expected output:
```
=== mediaflow smoke test ===
Dropping tests/fixtures/test-speech.m4a into workspace/1_input/ ...
Waiting for pipeline (timeout: 300s) ...
✓  Pipeline completed (47s)

  ✓  SRT transcript (2048 bytes)
  ✓  Summary markdown (891 bytes)
  ✓  Summary JSON (643 bytes)
  ✓  Processed WAV (7340032 bytes)  [workspace/2_processing/]
  ✓  Archived original (323584 bytes)
  ✓  SRT has content (42 lines)

=== Result: 6 passed, 0 failed ===
```

The test is idempotent — re-running cleans up previous outputs first.

### Useful Commands

```bash
# Check API health
curl http://localhost:8080/health

# Check pipeline status
curl http://localhost:8080/status/ | python3 -m json.tool

# List SRT files
curl http://localhost:8080/files/

# Manually push a test event (bypasses Redis, hits HTTP directly)
curl -X POST http://localhost:8080/events/stage-complete \
  -H "Content-Type: application/json" \
  -d '{"event": "task.submitted", "stem": "test01", "filename": "test01.m4a"}'

# Watch Redis stream live
redis-cli XREAD COUNT 10 STREAMS mediaflow:events 0

# Re-run summary stage only (tune Ollama prompts without re-running FFmpeg/Whisper)
source venv/bin/activate
python -m pipeline.rerun --stem lesson01 --from-stage summary

# Re-run from transcription (WAV exists in 2_processing/, skip FFmpeg)
python -m pipeline.rerun --stem lesson01 --from-stage transcription

# Rebuild after code change (Docker)
docker compose build api && docker compose up -d api

# Rebuild (Podman)
podman compose build api && podman compose up -d api
```

---

## Version Control (Trunk-Based Development)

This project uses **trunk-based development**: `main` is the single integration branch, always deployable.

### Rules

| Rule | Detail |
|------|--------|
| **`main` is always green** | Never commit broken code directly. If a change is large, use a short-lived branch. |
| **Short-lived branches only** | Branch off `main`, merge back within 1–2 days. Delete after merge. |
| **No long-lived feature branches** | All work lands on `main` regularly. Use feature flags in config if a half-done feature must be committed. |
| **Small, atomic commits** | One logical change per commit. Prefer many small commits over one large one. |
| **Do not push unless asked** | The user explicitly confirms each push. Never `git push` autonomously. |
| **No force-push to `main`** | Non-negotiable. |

### Branch Naming

```
feat/p1-4-incremental-rerun
fix/whisper-timeout-handling
chore/update-deps
```

### Commit Message Format

```
<type>(<scope>): <one-line summary>

type: feat | fix | chore | docs | refactor | test
scope: pipeline | api | web | stages | consumer (optional)

Examples:
  feat(stages): add segment verification against whisper-large-v3
  fix(consumer): handle Redis NOGROUP error on first startup
  chore: bump ollama to 0.4
  docs: update CLAUDE.md with diarization notes
```

### Workflow for a New Feature

```bash
# 1. Start from a clean main
git checkout main && git pull

# 2. Short-lived branch
git checkout -b feat/p1-4-incremental-rerun

# 3. Small commits as you go
git add pipeline/rerun.py pipeline/stages.py
git commit -m "feat(pipeline): add --from-stage flag to skip completed stages"

# 4. Keep branch up to date if main moves (rebase preferred over merge)
git fetch origin && git rebase origin/main

# 5. Merge back (fast-forward when possible, no merge commits for small changes)
git checkout main
git merge --ff-only feat/p1-4-incremental-rerun

# 6. Delete branch
git branch -d feat/p1-4-incremental-rerun

# 7. Push only when user confirms
# git push origin main
```

### When to Use a Branch vs Direct Commit to Main

| Situation | Approach |
|-----------|----------|
| Single-file fix, < 30 lines | Direct commit to `main` |
| Multi-file feature, can be done in one session | Direct commit to `main` (keep it atomic) |
| Multi-session work or touches > 3 files | Short-lived branch |
| Experimental (might be reverted) | Short-lived branch |

### Tagging Releases

There are no formal releases yet. When the pipeline is stable end-to-end (all Phase 0–2 working in production), tag `v0.1.0`:

```bash
git tag -a v0.1.0 -m "Phase 0-2 complete: Docker + pipeline + dashboard + SRT browser"
# git push origin v0.1.0   # only when user confirms
```

---

## Coding Conventions

- **No comments** unless the WHY is non-obvious. Never narrate what the code does.
- **Blocking functions** (FFmpeg subprocess, httpx calls, ollama.chat) belong in `pipeline/stages.py` and must be called via the thread pool in `watcher.py`, never in async context.
- **Async functions** belong in `api/`. Use `aiosqlite` for DB access.
- **Event processing logic** lives in `api/event_processor.py`. Both the HTTP route (`api/routes/events.py`) and the Redis consumer (`api/mq/consumer.py`) call `process_event()` — do not duplicate logic between them.
- **No Redis on the API side except in `api/mq/consumer.py`**. The rest of the API is Redis-unaware.
- **config.yaml is gitignored**. `config.yaml.example` is the committed template.
- Do not push to remote unless explicitly asked.
