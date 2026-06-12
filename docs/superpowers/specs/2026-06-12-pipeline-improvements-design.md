# Pipeline Improvements — Design Spec

**Date:** 2026-06-12  
**Scope:** mediaflow repo only (automate/Whisper server is out of scope)  
**Source:** Operational issues observed during wiki ingest workflow

---

## Background

Four operational problems were identified during a 3-hour recording ingest:

1. `/status` showed an empty `processing[]` list while transcription was running
2. `watcher.log` went silent for 35 minutes between `preprocess done` and SRT output
3. Two watcher processes ran simultaneously after a manual restart
4. Every job ran all stages including Ollama summarise, even when only the SRT was needed

---

## Item A — `transcribing` status in `/status`

### Problem

`stage.started/transcribe` is published to Redis before the blocking `httpx.post()` call, so the task *should* appear in `processing[]`. In practice, the task becomes invisible because:

- `started_at` is only set on `stage.started/preprocess`, leaving it NULL during transcription
- If the API Redis consumer has any lag, the task briefly has `current_stage=preprocess (already done)` with no forward progress signal

There is no distinct status to differentiate "in Ollama" from "in Whisper", making it impossible to tell at a glance that a 35-minute block is transcription.

### Design

Add a `transcribing` value to `tasks.status`.

**`api/event_processor.py`** — extend `_STATUS_MAP` and add stage-specific branching:

```python
_STATUS_MAP = {
    "task.submitted":  "submitted",
    "stage.started":   "processing",   # default; overridden below for transcribe
    "stage.completed": "processing",
    "task.completed":  "completed",
    "task.failed":     "failed",
}
```

After computing `new_status`, override for transcription start:

```python
if event == "stage.started" and fields.get("stage") == "transcribe":
    new_status = "transcribing"
```

When `stage.completed/transcribe` arrives, `_STATUS_MAP` maps it back to `"processing"` — no change needed there.

**`api/db.py`** — `get_status_overview()` includes `transcribing` in the processing query:

```python
"SELECT * FROM tasks WHERE status IN ('processing', 'transcribing') ORDER BY started_at DESC"
```

No schema migration required (SQLite stores status as TEXT with no enum constraint).

**Files changed:** `api/event_processor.py`, `api/db.py`  
**Restart required:** api container

---

## Item B — Heartbeat log during transcription

### Problem

The `transcribe()` stage blocks on `httpx.post()` for up to 30 minutes. During this time, `watcher.log` produces no output. Operators cannot distinguish "running normally" from "hung".

### Design

Start a daemon thread inside `transcribe()` immediately before the blocking POST. The thread logs every 60 seconds and stops when the POST completes (success or error).

```python
import threading, time

def transcribe(audio_path, stem, output_dir, cfg):
    ...
    _t0 = time.monotonic()
    _stop = threading.Event()

    def _heartbeat():
        while not _stop.wait(60):
            log.info("transcription in progress: %s (%.0fs elapsed)", stem, time.monotonic() - _t0)

    threading.Thread(target=_heartbeat, daemon=True, name=f"hb-{stem}").start()
    try:
        resp = httpx.post(...)
    finally:
        _stop.set()
    ...
```

The heartbeat thread is daemon=True so it cannot prevent process exit. The `finally` block ensures `_stop.set()` is called even if the POST raises.

**Files changed:** `pipeline/stages.py`  
**Restart required:** watcher

---

## Item C — PID lock in start-pipeline.sh

### Problem

Running `scripts/start-pipeline.sh` without stopping the previous watcher starts a second process. Both watchers pick up new files from `1_input/`, causing duplicate pipeline runs.

### Design

Write a PID file to `/tmp/mediaflow-watcher.pid` at startup. If the file exists and the recorded PID is alive, refuse to start.

```bash
PID_FILE="/tmp/mediaflow-watcher.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Watcher already running (PID $OLD_PID). Stop it first: kill $OLD_PID"
        exit 1
    fi
fi

echo $$ > "$PID_FILE"
trap "rm -f $PID_FILE" EXIT
```

`kill -0` checks process existence without sending a signal. The `trap EXIT` removes the PID file on normal exit, `Ctrl-C`, and unhandled signals — so a stale file from a crashed watcher does not block the next start (the `kill -0` check handles that case).

**Files changed:** `scripts/start-pipeline.sh`  
**Restart required:** n/a

---

## Item D — `stop_after_stage` config flag

### Problem

Every pipeline run executes all enabled stages including `summarize` (Ollama, ~10–15 min). For workflows that only need the SRT, this is unnecessary.

### Design

Add an optional `pipeline.stop_after_stage` key to `config.yaml`. When set, `runner.execute()` halts after the named stage completes; all stages after it are skipped even if enabled.

**`config.yaml.example`:**

```yaml
pipeline:
  # stop_after_stage: transcribe   # uncomment to skip summarize and later stages
```

**`pipeline/runner.py`** — add `stop_after` parameter to `execute()`:

```python
def execute(cfg, ctx, pub, from_stage=None, stop_after=None):
    ...
    for s in stage_cfgs:
        ...
        ctx, extra = STAGE_RUNNERS[sid](ctx, cfg)
        pub.publish("stage.completed", ctx["stem"], stage=sid, **extra)
        if stop_after and sid == stop_after:
            log.info("stop_after_stage=%s reached, halting pipeline", stop_after)
            break
    return ctx
```

**`pipeline/watcher.py`** — read from config and pass through:

```python
stop_after = cfg.get("pipeline", {}).get("stop_after_stage")
ctx = runner.execute(cfg, ctx, pub, stop_after=stop_after)
```

`rerun.py` calls `runner.execute()` via the same `cfg` dict, so it respects the setting without any additional change.

The named stage must be a valid stage ID. If `stop_after_stage` names a disabled stage, the pipeline runs past it (the stage is skipped, the break never fires) — this is the expected behaviour since a disabled stage never "completes".

**Files changed:** `pipeline/runner.py`, `pipeline/watcher.py`, `config.yaml.example`  
**Restart required:** watcher

---

## Change Summary

| Item | Files | Lines (est.) | Restart |
|------|-------|-------------|---------|
| A: transcribing status | `api/event_processor.py`, `api/db.py` | ~5 | api container |
| B: heartbeat log | `pipeline/stages.py` | ~12 | watcher |
| C: PID lock | `scripts/start-pipeline.sh` | ~10 | — |
| D: stop_after_stage | `pipeline/runner.py`, `pipeline/watcher.py`, `config.yaml.example` | ~8 | watcher |

**Total:** ~35 lines. No schema migration. No new dependencies.
