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

> **⚠️ Docker path constraint:** The API runs inside Docker. `path` must be a path **visible to the API container** — i.e., inside a volume-mounted directory. On this deployment the API mounts `./workspace:/workspace`, so only paths under `workspace/` resolve correctly. A host path like `/Users/you/recording.m4a` will return 404 even if the file exists. Either copy the file into `workspace/` first, or use the web upload at http://localhost:3000/upload for files stored elsewhere.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | ✓ | Container-visible absolute path (must be inside a volume mount — see note above) |
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
- `409` — Task is currently active (processing/submitted/queued)
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

### Stats

#### `GET /stats/overview` — Aggregate counts + speaker breakdown

Returns task totals from the DB and per-speaker duration aggregated across all `*_diarization.json` files in `workspace/3_output/`.

```bash
curl http://localhost:8080/stats/overview | python3 -m json.tool
```

```json
{
  "total_tasks": 42,
  "total_duration_sec": 170580.0,
  "success_rate": 0.976,
  "speakers": [
    {"label": "SPEAKER_00", "seconds": 82000.1, "pct": 0.481},
    {"label": "SPEAKER_01", "seconds": 52000.3, "pct": 0.305}
  ]
}
```

`total_duration_sec` is the sum of `duration_sec` from the tasks table. If tasks pre-date duration tracking, this will be 0 for those records.

---

#### `GET /stats/keywords` — Top topics across all recordings

Scans all `*_summary.json` files in `workspace/3_output/`, counts occurrences of each `topic_segments[].topic`, returns top 10 sorted by frequency.

```bash
curl http://localhost:8080/stats/keywords | python3 -m json.tool
```

```json
[
  {"topic": "機器學習", "count": 14},
  {"topic": "反向傳播", "count": 9}
]
```

---

### Audio

#### `GET /files/{stem}/audio` — Serve processed WAV

Returns the cleaned WAV from `workspace/2_processing/{stem}_clean.wav`. Supports HTTP Range requests (required for browser `<audio>` seeking). Returns 404 if the WAV doesn't exist (e.g. deleted by lifecycle policy).

```bash
# Download audio
curl http://localhost:8080/files/lecture01/audio -o lecture01.wav

# Fetch a 64 KB chunk (browser seeking)
curl -H "Range: bytes=0-65535" http://localhost:8080/files/lecture01/audio -o chunk.wav
# → 206 Partial Content, Content-Range: bytes 0-65535/<total>
```

The web dashboard proxies this endpoint at `http://localhost:3000/files/{stem}/audio` and uses it to drive the in-browser audio player in the SRT viewer.

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

### Full restart after a failed task

If a task failed mid-way and the original file is in `workspace/4_archive/`:
```bash
curl -X POST http://localhost:8080/tasks/STEM/runs \
  -H "Content-Type: application/json" \
  -d '{}'
```
`rerun.py` searches both `4_archive/` and `1_input/` for the original file.

---

## Log locations

| Process | Log path | How to watch |
|---------|----------|-------------|
| Pipeline watcher (host) | `data/logs/watcher.log` | `tail -f data/logs/watcher.log` |
| API container | stdout → `docker logs mediaflow_api_1` | `docker logs -f mediaflow_api_1` |
| Web container | stdout → `docker logs mediaflow_web_1` | `docker logs -f mediaflow_web_1` |

The watcher log shows per-stage progress (`stage.started preprocess`, `stage.completed transcribe`, etc.) and is the fastest way to see what the pipeline is doing at any moment.

---

## Data Lifecycle

Configure retention in `config.yaml` under `lifecycle:`. Restart the watcher after changes.

| Tier | Config key | Default | Notes |
|------|-----------|---------|-------|
| `2_processing/_clean.wav` | `lifecycle.wav` | `immediate` | ~200 MB/file; regenerated by re-running `preprocess` |
| `4_archive/` originals | `lifecycle.archive` | `30d` | Set `"forever"` if no other backup exists |
| `3_output/` SRT + summaries | `lifecycle.output` | `forever` | Small files; keep forever recommended |
| MinIO input bucket | `LIFECYCLE_MINIO_INPUT` env var | `forever` | Uploaded originals on MinIO |
| MinIO output bucket | `LIFECYCLE_MINIO_OUTPUT` env var | `forever` | Output backups on MinIO |

**Values:** `"immediate"` | `"7d"` / `"30d"` / `"90d"` | `"forever"` / `"keep"`

**Manual one-shot cleanup:**
```bash
# Preview (no deletions)
python -m pipeline.cleanup --dry-run

# Clean only old WAVs
python -m pipeline.cleanup --target wav

# Clean everything per policy
python -m pipeline.cleanup
```

**Note:** `archive: immediate` deletes the original recording after pipeline success. If you rerun from `preprocess` later, you will need to re-submit the original file.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `409 Task 'X' already active` | Task is still processing | Wait for it to complete, or `DELETE /tasks/X` first |
| `409 Task 'X' already active` (on rerun) | Task is currently processing/queued | Wait for current run to complete before rerunning |
| `404 File not found` | Path doesn't exist on the host | The file must be accessible to the API process; check `WORKSPACE_DIR` mount |
| `415 Unsupported format` | File extension not in `.mp4,.m4a,.mp3,.wav,.flac` | Convert the file first, or add extension to `PIPELINE_SUPPORTED_FORMATS` env var |
| `422 Unknown stage` | Typo in `from_stage` | Valid values: `preprocess`, `transcribe`, `verify_segments`, `correct_srt`, `diarize`, `summarize`, `detect_chapters` |
| Rerun queued but nothing happens | Watcher not running, or `DB_PATH` env var mismatch | Start `bash scripts/start-pipeline.sh`; verify `DB_PATH` matches the mounted `data/pipeline.db` |
| Dashboard cancel button does nothing | Web container not rebuilt after code change | `docker compose build web && docker compose up -d web` |
| `rerun-from-transcribe` fails (WAV missing) | `lifecycle.wav=immediate` deletes WAV on completion | Use full restart (omit `from_stage`) — FFmpeg regenerates the WAV |
