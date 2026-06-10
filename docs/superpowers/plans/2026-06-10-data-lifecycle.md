# Data Lifecycle Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable data retention system that automatically expires WAV intermediates, original recordings, pipeline outputs, and MinIO objects — without breaking the pipeline if files are already gone.

**Architecture:** `pipeline/lifecycle.py` provides shared filesystem helpers used by the watcher and CLI; `api/lifecycle.py` provides the same parse logic for the API container (which can't import from `pipeline/`). The watcher runs an hourly `_lifecycle_poller` thread for time-based cleanup and handles `immediate` deletions on completion. The API runs an hourly asyncio cleanup task for `3_output/` and sets MinIO bucket lifecycle rules at startup.

**Tech Stack:** Python stdlib (`pathlib`, `threading`, `time`, `datetime`), aiosqlite, boto3 (MinIO S3 lifecycle API), FastAPI lifespan.

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `pipeline/lifecycle.py` | Create | `parse_retention`, `scan_and_expire`, `safe_unlink` |
| `pipeline/cleanup.py` | Create | CLI `python -m pipeline.cleanup` |
| `api/lifecycle.py` | Create | `parse_retention` for API container (no pipeline imports) |
| `api/cleanup.py` | Create | Async output cleanup loop |
| `api/minio_client.py` | Modify | Add `set_bucket_lifecycle` method to `MinIOClient` |
| `api/main.py` | Modify | Start cleanup task + set MinIO lifecycle in lifespan |
| `pipeline/watcher.py` | Modify | Replace `cleanup_wav` logic + add `_lifecycle_poller` thread |
| `docker-compose.yml` | Modify | Add `LIFECYCLE_*` env vars to api service |
| `config.yaml.example` | Modify | Replace `pipeline.cleanup_wav` with `lifecycle:` section |
| `tests/test_lifecycle.py` | Create | Tests for `pipeline/lifecycle.py` |
| `tests/test_api_lifecycle.py` | Create | Tests for `api/lifecycle.py` + `api/cleanup.py` |

---

## Task 1: `pipeline/lifecycle.py` + tests

**Files:**
- Create: `pipeline/lifecycle.py`
- Create: `tests/test_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lifecycle.py`:

```python
"""Tests for pipeline/lifecycle.py — parse_retention and scan_and_expire."""
import os
import time
from datetime import timedelta
from pathlib import Path

from pipeline.lifecycle import parse_retention, scan_and_expire


def test_parse_retention_immediate():
    assert parse_retention("immediate") == timedelta(0)


def test_parse_retention_days():
    assert parse_retention("30d") == timedelta(days=30)
    assert parse_retention("90d") == timedelta(days=90)


def test_parse_retention_forever():
    assert parse_retention("forever") is None
    assert parse_retention("keep") is None
    assert parse_retention("") is None


def test_scan_and_expire_deletes_old_file(tmp_path):
    f = tmp_path / "old_clean.wav"
    f.write_bytes(b"data")
    old_ts = time.time() - 40 * 86400
    os.utime(f, (old_ts, old_ts))
    deleted = scan_and_expire(tmp_path, timedelta(days=30))
    assert f in deleted
    assert not f.exists()


def test_scan_and_expire_skips_fresh_file(tmp_path):
    f = tmp_path / "new_clean.wav"
    f.write_bytes(b"data")
    deleted = scan_and_expire(tmp_path, timedelta(days=30))
    assert deleted == []
    assert f.exists()


def test_scan_and_expire_tolerates_missing_file(tmp_path):
    # Directory exists but no matching files — must not raise
    deleted = scan_and_expire(tmp_path, timedelta(0))
    assert deleted == []


def test_scan_and_expire_dry_run_no_delete(tmp_path):
    f = tmp_path / "old.wav"
    f.write_bytes(b"data")
    old_ts = time.time() - 10 * 86400
    os.utime(f, (old_ts, old_ts))
    deleted = scan_and_expire(tmp_path, timedelta(days=5), dry_run=True)
    assert f in deleted
    assert f.exists()  # not actually deleted


def test_scan_and_expire_forever_returns_empty(tmp_path):
    f = tmp_path / "old.wav"
    f.write_bytes(b"data")
    deleted = scan_and_expire(tmp_path, retention=None)
    assert deleted == []
    assert f.exists()


def test_scan_and_expire_nonexistent_dir():
    # Missing directory must not raise
    deleted = scan_and_expire(Path("/nonexistent/dir/xyz"), timedelta(days=1))
    assert deleted == []


def test_scan_and_expire_stem_pattern(tmp_path):
    wav = tmp_path / "stem_clean.wav"
    mp4 = tmp_path / "stem.mp4"
    for f in [wav, mp4]:
        f.write_bytes(b"data")
        old_ts = time.time() - 60 * 86400
        os.utime(f, (old_ts, old_ts))
    deleted = scan_and_expire(tmp_path, timedelta(days=30), stem_pattern="*_clean.wav")
    assert wav in deleted
    assert mp4 not in deleted
    assert mp4.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source venv/bin/activate
python -m pytest tests/test_lifecycle.py -v
```
Expected: FAILED — `ModuleNotFoundError: No module named 'pipeline.lifecycle'`

- [ ] **Step 3: Create `pipeline/lifecycle.py`**

```python
"""Lifecycle helpers — parse retention config and expire files by age."""
import logging
import time
from datetime import timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def parse_retention(value: str) -> "timedelta | None":
    """Parse a retention string into a timedelta or None (keep forever).

    "immediate" → timedelta(0), "Nd" → timedelta(N days), "forever"/"keep"/"" → None.
    """
    if not value or value in ("forever", "keep"):
        return None
    if value == "immediate":
        return timedelta(0)
    if isinstance(value, str) and value.endswith("d"):
        try:
            return timedelta(days=int(value[:-1]))
        except ValueError:
            pass
    log.warning("Unrecognised retention value %r — treating as forever", value)
    return None


def safe_unlink(path: Path, label: str = "") -> bool:
    """Delete path. Returns True if deleted, False if already gone or other error."""
    try:
        path.unlink()
        log.info("Deleted %s: %s", label, path.name)
        return True
    except FileNotFoundError:
        log.debug("Already gone %s: %s", label, path.name)
        return False
    except OSError as exc:
        log.warning("Could not delete %s %s: %s", label, path.name, exc)
        return False


def scan_and_expire(
    directory: Path,
    retention: "timedelta | None",
    stem_pattern: str = "*",
    dry_run: bool = False,
) -> list:
    """Delete files in directory older than retention. Returns list of deleted (or would-delete) paths.

    retention=None means keep forever. retention=timedelta(0) means delete all matching files.
    FileNotFoundError on any individual file is caught and logged at DEBUG — does not stop the scan.
    """
    if retention is None:
        return []
    if not directory.exists():
        log.debug("scan_and_expire: directory does not exist: %s", directory)
        return []

    cutoff = time.time() - retention.total_seconds()
    deleted = []

    for f in directory.glob(stem_pattern):
        if not f.is_file():
            continue
        try:
            mtime = f.stat().st_mtime
        except FileNotFoundError:
            log.debug("Already gone during scan: %s", f.name)
            continue
        if mtime <= cutoff:
            if dry_run:
                log.info("[dry-run] Would delete: %s", f)
                deleted.append(f)
            elif safe_unlink(f, "expired"):
                deleted.append(f)

    return deleted
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_lifecycle.py -v
```
Expected: 9 PASSED

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v --tb=short -q
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pipeline/lifecycle.py tests/test_lifecycle.py
git commit -m "feat(pipeline): add lifecycle.py with parse_retention and scan_and_expire"
```

---

## Task 2: `api/lifecycle.py` + `api/cleanup.py` + tests

**Files:**
- Create: `api/lifecycle.py`
- Create: `api/cleanup.py`
- Create: `tests/test_api_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_lifecycle.py`:

```python
"""Tests for api/lifecycle.py (parse_retention) and api/cleanup.py (output cleanup)."""
import asyncio
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


# ── api/lifecycle.py ──────────────────────────────────────────────────────────

from api.lifecycle import parse_retention as api_parse


def test_api_parse_retention_days():
    assert api_parse("30d") == timedelta(days=30)
    assert api_parse("7d") == timedelta(days=7)


def test_api_parse_retention_forever():
    assert api_parse("forever") is None
    assert api_parse("keep") is None
    assert api_parse("") is None


def test_api_parse_retention_immediate():
    assert api_parse("immediate") == timedelta(0)


def test_api_parse_retention_invalid_returns_none():
    assert api_parse("badvalue") is None


# ── api/cleanup.py ────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_output(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())
    output_dir = tmp_path / "3_output"
    output_dir.mkdir()
    return db_mod, output_dir


def _age_file(path: Path, days: int) -> None:
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def _make_stem(output_dir: Path, stem: str, age_days: int = 0) -> None:
    srt = output_dir / f"{stem}.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    (output_dir / f"{stem}_summary.md").write_text("# Summary")
    if age_days > 0:
        _age_file(srt, age_days)
        _age_file(output_dir / f"{stem}_summary.md", age_days)


def test_output_cleanup_deletes_all_stem_files(tmp_output):
    db_mod, output_dir = tmp_output
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("old_lesson", status="completed", filename="old_lesson.m4a")
    )
    _make_stem(output_dir, "old_lesson", age_days=40)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )
    assert not (output_dir / "old_lesson.srt").exists()
    assert not (output_dir / "old_lesson_summary.md").exists()


def test_output_cleanup_removes_db_row(tmp_output):
    db_mod, output_dir = tmp_output
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("old_lesson2", status="completed", filename="old_lesson2.m4a")
    )
    _make_stem(output_dir, "old_lesson2", age_days=40)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("old_lesson2"))
    assert task is None


def test_output_cleanup_skips_fresh_stem(tmp_output):
    _, output_dir = tmp_output
    _make_stem(output_dir, "fresh", age_days=0)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )
    assert (output_dir / "fresh.srt").exists()


def test_output_cleanup_tolerates_missing_files(tmp_output):
    _, output_dir = tmp_output
    _make_stem(output_dir, "partial", age_days=40)
    (output_dir / "partial_summary.md").unlink()  # simulate already-gone file

    from api.cleanup import run_output_cleanup
    # Must not raise
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )


def test_output_cleanup_noop_for_forever(tmp_output):
    _, output_dir = tmp_output
    _make_stem(output_dir, "keep_me", age_days=400)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, None)
    )
    assert (output_dir / "keep_me.srt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_api_lifecycle.py -v
```
Expected: FAILED — `ModuleNotFoundError: No module named 'api.lifecycle'`

- [ ] **Step 3: Create `api/lifecycle.py`**

```python
"""Lifecycle retention parsing for the API container (no pipeline imports)."""
import logging
from datetime import timedelta

log = logging.getLogger(__name__)


def parse_retention(value: str) -> "timedelta | None":
    """Parse a retention string. Returns timedelta, or None for keep-forever.

    "immediate" → timedelta(0), "Nd" → timedelta(N), "forever"/"keep"/"" → None.
    Invalid values return None (safe fallback — no data deleted).
    """
    if not value or value in ("forever", "keep"):
        return None
    if value == "immediate":
        return timedelta(0)
    if isinstance(value, str) and value.endswith("d"):
        try:
            return timedelta(days=int(value[:-1]))
        except ValueError:
            pass
    log.warning("Unrecognised retention value %r — treating as forever", value)
    return None
```

- [ ] **Step 4: Create `api/cleanup.py`**

```python
"""Async output cleanup — expires 3_output/ stems older than configured retention."""
import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path

from api import db

log = logging.getLogger(__name__)

_OUTPUT_SUFFIXES = [
    ".srt",
    "_summary.md",
    "_summary.json",
    "_segments.json",
    "_diarization.json",
    "_speaker_names.json",
    "_chapters.json",
]


async def run_output_cleanup(output_dir: Path, retention: "timedelta | None") -> None:
    """Scan output_dir for expired stems and delete their files + DB rows."""
    if retention is None:
        return
    if not output_dir.exists():
        return

    cutoff = time.time() - retention.total_seconds()

    for srt in list(output_dir.glob("*.srt")):
        try:
            mtime = srt.stat().st_mtime
        except FileNotFoundError:
            log.debug("Already gone during scan: %s", srt.name)
            continue

        if mtime > cutoff:
            continue

        stem = srt.stem
        for suffix in _OUTPUT_SUFFIXES:
            path = output_dir / f"{stem}{suffix}"
            try:
                await asyncio.to_thread(path.unlink)
                log.info("Deleted expired output: %s", path.name)
            except FileNotFoundError:
                log.debug("Already gone: %s", path.name)
            except OSError as exc:
                log.warning("Could not delete %s: %s", path.name, exc)

        await db.delete_task(stem)
        log.info("Pruned DB row for expired stem: %s", stem)


async def cleanup_loop(output_dir: Path, retention: "timedelta | None") -> None:
    """Run output cleanup every hour. Stopped by asyncio task cancellation."""
    log.info("Output cleanup loop started (retention=%s)", retention)
    while True:
        try:
            await run_output_cleanup(output_dir, retention)
        except asyncio.CancelledError:
            log.info("Output cleanup loop stopped")
            return
        except Exception as exc:
            log.warning("Output cleanup error: %s", exc)
        await asyncio.sleep(3600)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_api_lifecycle.py -v
```
Expected: all tests PASS

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v --tb=short -q
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add api/lifecycle.py api/cleanup.py tests/test_api_lifecycle.py
git commit -m "feat(api): add lifecycle.py and cleanup.py for output retention"
```

---

## Task 3: Watcher changes

**Files:**
- Modify: `pipeline/watcher.py`

No automated unit tests — behaviour verified by Task 1's lifecycle module tests + manual smoke test.

- [ ] **Step 1: Add `pipeline.lifecycle` import at top of `pipeline/watcher.py`**

Add after the existing imports (after `from pipeline import runner`):

```python
from pipeline.lifecycle import parse_retention, scan_and_expire, safe_unlink
```

- [ ] **Step 2: Replace old `cleanup_wav` block in `_run_pipeline`**

Find the current block (lines 49-53):
```python
        if cfg.get("pipeline", {}).get("cleanup_wav", False):
            wav = ctx["audio_path"]
            if wav.exists():
                wav.unlink()
                log.info("Cleaned up %s", wav.name)
```

Replace with the new lifecycle-aware version. Insert it after `path.rename(archive_dir / path.name)` and before `pub.publish("task.completed", ...)`:

```python
        lc = cfg.get("lifecycle", {})
        # backward-compat: pipeline.cleanup_wav=true treated as lifecycle.wav=immediate
        _old_cleanup = cfg.get("pipeline", {}).get("cleanup_wav", False)
        wav_setting = lc.get("wav") or ("immediate" if _old_cleanup else "keep")
        if parse_retention(wav_setting) == timedelta(0):
            safe_unlink(ctx["audio_path"], "wav")

        if parse_retention(lc.get("archive", "forever")) == timedelta(0):
            safe_unlink(ws / "4_archive" / path.name, "archive")
```

Add `from datetime import timedelta` to the imports at the top of the file.

- [ ] **Step 3: Add `_lifecycle_poller` function**

Add after `_rerun_poller` function, before `class InputHandler`:

```python
def _lifecycle_poller(cfg: dict, ws: Path, stop_event: threading.Event) -> None:
    """Hourly scan of 2_processing/ and 4_archive/ for time-based retention rules."""
    log.info("Lifecycle poller started")
    while not stop_event.is_set():
        try:
            lc = cfg.get("lifecycle", {})
            wav_ret = parse_retention(lc.get("wav", "keep"))
            arch_ret = parse_retention(lc.get("archive", "forever"))
            # Only scan for time-based retention; immediate is handled on-completion
            if wav_ret is not None and wav_ret.total_seconds() > 0:
                scan_and_expire(ws / "2_processing", wav_ret, stem_pattern="*_clean.wav")
            if arch_ret is not None and arch_ret.total_seconds() > 0:
                scan_and_expire(ws / "4_archive", arch_ret)
        except Exception as exc:
            log.warning("Lifecycle poller error: %s", exc)
        stop_event.wait(3600)
    log.info("Lifecycle poller stopped")
```

- [ ] **Step 4: Update `run()` to start the lifecycle poller**

In `run()`, after the `poller.start()` line (rerun poller), add:

```python
    lc_poller = threading.Thread(
        target=_lifecycle_poller,
        args=(cfg, Path(cfg["pipeline"]["workspace_dir"]), stop_ev),
        daemon=True,
        name="lifecycle-poller",
    )
    lc_poller.start()
```

The complete updated block in `run()` (after `pub = EventPublisher(cfg)`) should now be:

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

    lc_poller = threading.Thread(
        target=_lifecycle_poller,
        args=(cfg, Path(cfg["pipeline"]["workspace_dir"]), stop_ev),
        daemon=True,
        name="lifecycle-poller",
    )
    lc_poller.start()
```

- [ ] **Step 5: Verify syntax**

```bash
source venv/bin/activate
python -c "import pipeline.watcher; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v --tb=short -q
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add pipeline/watcher.py
git commit -m "feat(pipeline): add lifecycle poller thread to watcher; replace cleanup_wav with lifecycle config"
```

---

## Task 4: API changes — MinIO lifecycle + output cleanup task

**Files:**
- Modify: `api/minio_client.py`
- Modify: `api/main.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add `set_bucket_lifecycle` to `MinIOClient` in `api/minio_client.py`**

Append this method to the `MinIOClient` class (after `presign_get_url`):

```python
    def set_bucket_lifecycle(self, bucket: str, days: int) -> None:
        """Set S3 expiration lifecycle rule on bucket. Idempotent — safe to call on every startup."""
        self._s3.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={
                "Rules": [{
                    "ID": "mediaflow-auto-expire",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": days},
                }]
            },
        )
```

- [ ] **Step 2: Update `api/main.py` lifespan**

Add these imports at the top of `api/main.py`:

```python
import os
from pathlib import Path
from api import cleanup
from api.lifecycle import parse_retention
```

Replace the lifespan function with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    await reconcile()

    minio_mod.init_client()
    try:
        minio_mod.get_client().ensure_buckets()
    except Exception as exc:
        logging.getLogger(__name__).warning("MinIO unavailable on startup: %s", exc)

    # Set MinIO bucket lifecycle rules
    for bucket, env_key in [
        (minio_mod.INPUT_BUCKET, "LIFECYCLE_MINIO_INPUT"),
        (minio_mod.OUTPUT_BUCKET, "LIFECYCLE_MINIO_OUTPUT"),
    ]:
        days_str = os.getenv(env_key, "forever")
        ret = parse_retention(days_str)
        if ret is not None and ret.total_seconds() > 0:
            try:
                minio_mod.get_client().set_bucket_lifecycle(bucket, int(ret.total_seconds() // 86400))
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Could not set MinIO lifecycle for %s: %s", bucket, exc
                )

    # Output cleanup loop
    output_retention = parse_retention(os.getenv("LIFECYCLE_OUTPUT", "forever"))
    output_dir = Path(os.getenv("WORKSPACE_DIR", "./workspace")) / "3_output"
    cleanup_task = asyncio.create_task(cleanup.cleanup_loop(output_dir, output_retention))

    redis_task = asyncio.create_task(consumer.run())
    queue_task = asyncio.create_task(queue_consumer.run())
    yield
    for task in [cleanup_task, redis_task, queue_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 3: Add `LIFECYCLE_*` env vars to `docker-compose.yml`**

In the `api` service `environment:` section (after the existing `UPLOAD_MAX_CONCURRENT` line), add:

```yaml
      - LIFECYCLE_OUTPUT=${LIFECYCLE_OUTPUT:-forever}
      - LIFECYCLE_MINIO_INPUT=${LIFECYCLE_MINIO_INPUT:-forever}
      - LIFECYCLE_MINIO_OUTPUT=${LIFECYCLE_MINIO_OUTPUT:-forever}
```

- [ ] **Step 4: Run full suite**

```bash
python -m pytest tests/ -v --tb=short -q
```
Expected: all tests pass (MinIO lifecycle method not yet tested — that's the existing minio_client test pattern)

- [ ] **Step 5: Verify API syntax**

```bash
python -c "import ast; ast.parse(open('api/main.py').read()); print('OK')"
```

- [ ] **Step 6: Commit**

```bash
git add api/minio_client.py api/main.py docker-compose.yml
git commit -m "feat(api): set MinIO bucket lifecycle + start output cleanup loop at startup"
```

---

## Task 5: CLI `pipeline/cleanup.py`

**Files:**
- Create: `pipeline/cleanup.py`

- [ ] **Step 1: Create `pipeline/cleanup.py`**

```python
"""One-shot lifecycle cleanup — mirrors what the watcher's hourly poller does.

Usage:
    python -m pipeline.cleanup [--dry-run] [--target wav|archive|output|all]
"""
import argparse
import logging
from pathlib import Path

from pipeline.config import load
from pipeline.lifecycle import parse_retention, scan_and_expire


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply lifecycle policy to workspace data")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    parser.add_argument(
        "--target",
        choices=["wav", "archive", "output", "all"],
        default="all",
        help="Which data tier to clean (default: all)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load()
    ws = Path(cfg["pipeline"]["workspace_dir"])
    lc = cfg.get("lifecycle", {})

    deleted = []

    if args.target in ("wav", "all"):
        ret = parse_retention(lc.get("wav", "keep"))
        if ret is not None and ret.total_seconds() > 0:
            deleted += scan_and_expire(ws / "2_processing", ret, "*_clean.wav", args.dry_run)

    if args.target in ("archive", "all"):
        ret = parse_retention(lc.get("archive", "forever"))
        if ret is not None and ret.total_seconds() > 0:
            deleted += scan_and_expire(ws / "4_archive", ret, "*", args.dry_run)

    if args.target in ("output", "all"):
        ret = parse_retention(lc.get("output", "forever"))
        if ret is not None and ret.total_seconds() > 0:
            deleted += scan_and_expire(ws / "3_output", ret, "*", args.dry_run)

    prefix = "[dry-run] Would delete" if args.dry_run else "Deleted"
    print(f"{prefix} {len(deleted)} file(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test the CLI**

```bash
source venv/bin/activate
python -m pipeline.cleanup --dry-run
```
Expected: runs without error, prints `[dry-run] Would delete 0 file(s)` (nothing is old enough yet with default config).

```bash
python -m pipeline.cleanup --help
```
Expected: shows usage.

- [ ] **Step 3: Commit**

```bash
git add pipeline/cleanup.py
git commit -m "feat(pipeline): add cleanup CLI — python -m pipeline.cleanup [--dry-run]"
```

---

## Task 6: Config + docs

**Files:**
- Modify: `config.yaml.example`
- Modify: `docs/operations-manual.md`

- [ ] **Step 1: Update `config.yaml.example`**

Find the existing `pipeline:` section. Remove the `cleanup_wav` line added in a previous commit and replace the `whisper:` block:

First, find and remove:
```yaml
  cleanup_wav: false               # delete 2_processing/_clean.wav after success (saves ~200 MB/file; disables rerun-from-transcribe)
```

Then add a new `lifecycle:` top-level section after the `notification:` block (before `minio:`):

```yaml
lifecycle:
  # Retention policy for each data tier.
  # Values: "immediate" | "Nd" (e.g. "30d", "90d") | "forever" (default for all)
  # "immediate" = delete as soon as the pipeline stage that produced it completes.
  # Restart watcher after editing.
  wav:          immediate   # 2_processing/_clean.wav (~200 MB/file, regenerable via preprocess)
  archive:      30d         # 4_archive/ originals  — set "forever" if no other backup exists
  output:       forever     # 3_output/ SRT + summaries — small; "forever" recommended
  minio_input:  7d          # mediaflow-input MinIO bucket TTL (uploaded originals)
  minio_output: 90d         # mediaflow-output MinIO bucket TTL (output backups)
  # Note: enabling cleanup_wav=immediate + archive=immediate means rerun-from-transcribe
  # or rerun-from-preprocess will require re-uploading the original file.
```

- [ ] **Step 2: Update `docs/operations-manual.md`**

Add a new section before the Troubleshooting table:

```markdown
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

**Note:** `archive: immediate` deletes the original recording after pipeline success. If you rerun from `preprocess` later, you will need to re-submit the original file via the upload page or `POST /tasks`.
```

- [ ] **Step 3: Verify config.yaml.example syntax**

```bash
source venv/bin/activate
python -c "import yaml; yaml.safe_load(open('config.yaml.example')); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Run full suite one final time**

```bash
python -m pytest tests/ -v --tb=short -q
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add config.yaml.example docs/operations-manual.md
git commit -m "docs(config): add lifecycle: section to config.yaml.example and operations-manual"
```

---

## Final Verification

- [ ] **Rebuild API container** (api/lifecycle.py, api/cleanup.py, api/main.py, api/minio_client.py all changed)

```bash
docker compose build api && docker compose up -d api
# OR
podman-compose build api && podman-compose up -d api
```

- [ ] **Restart watcher** (pipeline/lifecycle.py, pipeline/watcher.py changed)

```bash
# Stop the running watcher (Ctrl+C if in foreground), then:
bash scripts/start-pipeline.sh
```

Expected in watcher log:
```
INFO  Rerun poller started ...
INFO  Lifecycle poller started
INFO  Watching workspace/1_input
```

- [ ] **End-to-end smoke test**

```bash
# Verify API lifecycle env vars are read
curl -s http://localhost:8080/health
# {"status": "ok"}

# Dry-run cleanup shows 0 deletions for fresh files
python -m pipeline.cleanup --dry-run
# [dry-run] Would delete 0 file(s)

# Test cleanup on old files by backdating a WAV
touch -t 202404010000 workspace/2_processing/test-speech_clean.wav
python -m pipeline.cleanup --target wav --dry-run
# [dry-run] Would delete 1 file(s)
```
