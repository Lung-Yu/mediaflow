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
