# Data Lifecycle Policy Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a configurable data retention policy that automatically expires WAV intermediates, original recordings, pipeline outputs, and MinIO objects — without manual intervention, and without breaking the pipeline if files are already gone.

**Architecture:** A single `lifecycle:` config section drives two cleanup components: (1) a `_lifecycle_poller` thread in `watcher.py` that handles host-filesystem directories (`2_processing/` and `4_archive/`) it has full write access to, and (2) a background asyncio task in the API that handles `3_output/` and sets MinIO bucket lifecycle rules at startup. A shared `pipeline/lifecycle.py` module provides parsing and scanning primitives used by both components and the CLI.

**Tech Stack:** Python stdlib (`pathlib`, `threading`, `time`), aiosqlite (API cleanup), aiobotocore/boto3 via existing `minio_client` (MinIO lifecycle), FastAPI lifespan (API background task).

---

## 1. Config Schema

New top-level `lifecycle:` section in `config.yaml` / `config.yaml.example`:

```yaml
lifecycle:
  wav:          immediate   # 2_processing/_clean.wav — immediate | keep
  archive:      30d         # 4_archive/ originals    — immediate | Nd | forever
  output:       forever     # 3_output/ SRT+summaries — Nd | forever
  minio_input:  7d          # mediaflow-input bucket  — Nd | forever
  minio_output: 90d         # mediaflow-output bucket — Nd | forever
```

**Retention value grammar:**

| Value | Meaning |
|-------|---------|
| `"immediate"` | Delete as soon as the pipeline stage that produced the file completes |
| `"Nd"` | Delete files older than N days (e.g. `"30d"`, `"90d"`, `"180d"`) |
| `"keep"` | Alias for `"forever"` — never delete |
| `"forever"` | Never delete |

`immediate` is only valid for `wav` and `archive`. Passing `immediate` to `output`, `minio_input`, or `minio_output` is silently treated as `"forever"` (safe fallback — no data loss).

**Backward compatibility:** the existing `pipeline.cleanup_wav: true/false` key continues to work. If both are present, `lifecycle.wav` takes precedence.

---

## 2. `pipeline/lifecycle.py` — Shared Module

```python
from datetime import timedelta
from pathlib import Path
import logging
import time

log = logging.getLogger(__name__)

def parse_retention(value: str) -> "timedelta | None":
    """Return timedelta for age limit, or None for 'keep forever'.

    "immediate" → timedelta(0), "Nd" → timedelta(N), "forever"/"keep" → None.
    """

def scan_and_expire(
    directory: Path,
    retention: "timedelta | None",
    stem_pattern: str = "*",
    dry_run: bool = False,
) -> list[Path]:
    """Delete files in directory older than retention. Returns list of deleted paths.

    Skips files that no longer exist (already deleted elsewhere) — logs a DEBUG line.
    retention=None means keep forever (returns []).
    retention=timedelta(0) means delete all matching files.
    """
```

**Error handling:** `FileNotFoundError` on unlink is caught and logged at DEBUG level — does not propagate. This is the key robustness requirement: already-removed files must not cause errors.

---

## 3. Watcher Changes (`pipeline/watcher.py`)

### 3a. On-completion: `immediate` cleanup

In `_run_pipeline`, after the archive step and before `pub.publish("task.completed", ...)`:

```python
lc = cfg.get("lifecycle", {})
wav_retention = parse_retention(lc.get("wav", cfg.get("pipeline", {}).get("cleanup_wav_compat", "keep")))
archive_retention = parse_retention(lc.get("archive", "forever"))

if wav_retention == timedelta(0):       # "immediate"
    _safe_unlink(ctx["audio_path"], "wav")

if archive_retention == timedelta(0):   # "immediate"
    # File is already renamed into 4_archive/ at this point; delete the archived copy.
    # rerun.py will not find the original after this — full restart would re-run preprocess.
    _safe_unlink(ws / "4_archive" / path.name, "archive")
```

`_safe_unlink(path, label)` — tries `path.unlink()`, catches `FileNotFoundError`, logs at DEBUG.

### 3b. `_lifecycle_poller` thread

New background daemon thread (alongside the existing `_rerun_poller`). Polls every 3600 s (1 hour):

```python
def _lifecycle_poller(cfg: dict, ws: Path, stop_event: threading.Event) -> None:
    log.info("Lifecycle poller started")
    while not stop_event.is_set():
        lc = cfg.get("lifecycle", {})
        _run_filesystem_lifecycle(ws, lc)
        stop_event.wait(3600)
    log.info("Lifecycle poller stopped")

def _run_filesystem_lifecycle(ws: Path, lc: dict) -> None:
    wav_ret   = parse_retention(lc.get("wav", "keep"))
    arch_ret  = parse_retention(lc.get("archive", "forever"))
    if wav_ret is not None and wav_ret != timedelta(0):
        scan_and_expire(ws / "2_processing", wav_ret, stem_pattern="*_clean.wav")
    if arch_ret is not None and arch_ret != timedelta(0):
        scan_and_expire(ws / "4_archive", arch_ret)
```

`stop_event.wait(3600)` instead of `time.sleep(3600)` allows clean shutdown when `stop_ev.set()` is called.

### 3c. `run()` change

Start lifecycle poller thread alongside the rerun poller, sharing the same `stop_ev`:

```python
lc_poller = threading.Thread(
    target=_lifecycle_poller,
    args=(cfg, Path(cfg["pipeline"]["workspace_dir"]), stop_ev),
    daemon=True,
    name="lifecycle-poller",
)
lc_poller.start()
```

---

## 4. API Changes

### 4a. MinIO lifecycle at startup (`api/minio_client.py`)

New function `set_bucket_lifecycle(bucket: str, days: int)` called from the API lifespan. Uses the existing boto3/MinIO client to call `put_bucket_lifecycle_configuration` with an expiration rule. If MinIO is unreachable, logs a warning and continues (non-fatal).

```python
def set_bucket_lifecycle(bucket: str, days: int) -> None:
    """Set S3 lifecycle expiration rule on bucket. No-op if days <= 0."""
```

### 4b. `api/cleanup.py` — Output cleanup background task

New module responsible for scanning `3_output/` and removing expired stems from both the filesystem and the DB:

```python
async def run_output_cleanup(output_dir: Path, retention: "timedelta | None") -> None:
    """Scan 3_output/ and delete stems older than retention. Runs filesystem ops in a thread."""

async def cleanup_loop(output_dir: Path, retention: "timedelta | None") -> None:
    """Run output cleanup every hour. Cancelled by the FastAPI lifespan cancel."""
```

**Logic for each stem:**
1. Find all `*.srt` files in `3_output/` to enumerate stems.
2. For each stem, use the `.srt` `mtime` as age proxy.
3. If `now - mtime > retention`: delete all `{stem}*` files using `asyncio.to_thread(path.unlink)`.
4. If any file is already gone (`FileNotFoundError`): log DEBUG, continue.
5. Delete the DB task row via `db.delete_task(stem)` (idempotent — no error if already gone).

### 4c. `api/main.py` lifespan changes

The API reads lifecycle settings from env vars (consistent with all other API config — no `config.yaml` in the container):

```
LIFECYCLE_OUTPUT=forever          # default: forever
LIFECYCLE_MINIO_INPUT=7d          # default: forever
LIFECYCLE_MINIO_OUTPUT=90d        # default: forever
```

In the lifespan:
```python
from pipeline.lifecycle import parse_retention   # NOTE: pipeline/ is NOT in api/Dockerfile
```

Wait — `pipeline/` is not copied into the API Docker image. So `parse_retention` must be defined in `api/lifecycle.py` (a thin copy or standalone, no pipeline imports). The watcher imports from `pipeline/lifecycle.py`; the API imports from `api/lifecycle.py`. Both implement the same `parse_retention` logic (~10 lines).

Lifespan additions:
```python
output_retention = parse_retention(os.getenv("LIFECYCLE_OUTPUT", "forever"))
cleanup_task = asyncio.create_task(
    cleanup.cleanup_loop(Path(os.getenv("WORKSPACE_DIR", "./workspace")) / "3_output", output_retention)
)
# Cancel on shutdown (alongside redis_task, queue_task)
```

MinIO lifecycle is set once at startup (not in the loop):
```python
for bucket, env_key in [(INPUT_BUCKET, "LIFECYCLE_MINIO_INPUT"), (OUTPUT_BUCKET, "LIFECYCLE_MINIO_OUTPUT")]:
    days_str = os.getenv(env_key, "forever")
    if days_str not in ("forever", "keep"):
        days = int(days_str.rstrip("d"))
        try:
            minio_mod.set_bucket_lifecycle(bucket, days)
        except Exception as exc:
            log.warning("Could not set MinIO lifecycle for %s: %s", bucket, exc)
```

---

## 5. `pipeline/cleanup.py` — CLI

```bash
python -m pipeline.cleanup [--dry-run] [--target wav|archive|output|all]
```

Reads `config.yaml`, calls `_run_filesystem_lifecycle` for host directories. For `output`, it directly scans `3_output/` on the host (no API needed).

`--dry-run` prints what would be deleted without deleting anything.

---

## 6. `docker-compose.yml` Changes

Add lifecycle env vars to the `api` service:

```yaml
- LIFECYCLE_OUTPUT=${LIFECYCLE_OUTPUT:-forever}
- LIFECYCLE_MINIO_INPUT=${LIFECYCLE_MINIO_INPUT:-7d}
- LIFECYCLE_MINIO_OUTPUT=${LIFECYCLE_MINIO_OUTPUT:-90d}
```

---

## 7. Tests

New `tests/test_lifecycle.py`:

- `test_parse_retention_immediate` — `timedelta(0)`
- `test_parse_retention_days` — `"30d"` → `timedelta(30)`
- `test_parse_retention_forever` — `None`
- `test_scan_and_expire_deletes_old_file`
- `test_scan_and_expire_skips_fresh_file`
- `test_scan_and_expire_tolerates_missing_file` — **key robustness test**: file deleted between scan and unlink must not raise
- `test_scan_and_expire_dry_run_no_delete`
- `test_output_cleanup_deletes_all_stem_files`
- `test_output_cleanup_removes_db_row`

---

## 8. `config.yaml.example` Changes

Replace `pipeline.cleanup_wav` (added last commit) with the full `lifecycle:` section. Add inline comments explaining each key.

---

## 9. Out of Scope

- Per-stem retention override (e.g. "keep this recording forever") — deferred
- Disk-space-triggered cleanup — deferred
- Log file rotation — separate concern
