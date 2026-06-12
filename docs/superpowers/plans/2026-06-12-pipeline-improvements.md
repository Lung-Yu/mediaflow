# Pipeline Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four operational problems: invisible tasks during transcription, silent watcher logs, duplicate watcher processes, and no way to stop the pipeline after transcription.

**Architecture:** Item A patches the event processor and DB query to use per-stage status names. Item B adds a daemon heartbeat thread inside `transcribe()`. Item C adds a PID lock to the start script. Item D adds `stop_after` to `runner.execute()` and wires it from config.

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, SQLite (TEXT status column — no migration needed), bash

---

## File Map

| File | Change |
|------|--------|
| `api/event_processor.py` | Replace flat `_STATUS_MAP` with `_STAGE_STATUS` + `_EVENT_STATUS`; skip DB status update on `stage.completed` |
| `api/db.py` | Expand `get_status_overview()` IN clause to include all stage-specific statuses |
| `pipeline/stages.py` | Add heartbeat daemon thread in `transcribe()` |
| `pipeline/runner.py` | Add `stop_after` parameter to `execute()` |
| `pipeline/watcher.py` | Read `pipeline.stop_after_stage` from cfg, pass to `runner.execute()` |
| `config.yaml.example` | Add commented `stop_after_stage` key under `pipeline:` |
| `scripts/start-pipeline.sh` | Add PID lock guard before launching watcher |
| `tests/test_event_processor_status.py` | New — unit tests for per-stage status mapping |
| `tests/test_runner_stop_after.py` | New — unit tests for `stop_after` behaviour |
| `tests/test_stages_heartbeat.py` | New — verify heartbeat thread starts and stops |

---

## Task 1: Per-stage status in event_processor

**Files:**
- Modify: `api/event_processor.py:13-19` (status mapping) and `:33` (new_status assignment)
- Create: `tests/test_event_processor_status.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_event_processor_status.py`:

```python
import asyncio
import os
import time

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _event(event, stem="s1", stage="", **kw):
    return {"event": event, "stem": stem, "stage": stage, "ts": str(time.time()), **kw}


def test_stage_started_preprocess_sets_preprocessing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="preprocess")))
    assert status == "preprocessing"


def test_stage_started_transcribe_sets_transcribing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="transcribe")))
    assert status == "transcribing"


def test_stage_started_summarize_sets_summarizing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="summarize")))
    assert status == "summarizing"


def test_stage_started_all_known_stages():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    expected = {
        "preprocess":      "preprocessing",
        "transcribe":      "transcribing",
        "verify_segments": "verifying",
        "correct_srt":     "correcting",
        "diarize":         "diarizing",
        "summarize":       "summarizing",
        "detect_chapters": "detecting_chapters",
    }
    for stage, want in expected.items():
        got = _run(ep.process_event(_event("stage.started", stem=stage, stage=stage)))
        assert got == want, f"stage={stage}: expected {want!r}, got {got!r}"


def test_stage_completed_does_not_change_db_status():
    import importlib
    import api.db as db_mod
    import api.event_processor as ep
    importlib.reload(ep)

    # Set status to transcribing via stage.started
    _run(ep.process_event(_event("stage.started", stage="transcribe")))
    row_before = _run(db_mod.get_task("s1"))
    assert row_before["status"] == "transcribing"

    # stage.completed must NOT change the status
    _run(ep.process_event(_event("stage.completed", stage="transcribe")))
    row_after = _run(db_mod.get_task("s1"))
    assert row_after["status"] == "transcribing"


def test_task_completed_sets_completed():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("task.completed")))
    assert status == "completed"


def test_task_failed_sets_failed():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("task.failed", error_msg="boom")))
    assert status == "failed"


def test_unknown_stage_started_falls_back_to_processing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="unknown_future_stage")))
    assert status == "processing"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/tygrus/Desktop/projects/mediaflow
source venv/bin/activate
pytest tests/test_event_processor_status.py -v
```

Expected: most tests FAIL because `_STATUS_MAP` still maps `stage.started` to `"processing"`.

- [ ] **Step 3: Replace status mapping in event_processor.py**

Replace lines 13–19 and line 33 in `api/event_processor.py`.

Old block (lines 13–19):
```python
_STATUS_MAP = {
    "task.submitted":  "submitted",
    "stage.started":   "processing",
    "stage.completed": "processing",
    "task.completed":  "completed",
    "task.failed":     "failed",
}
_TERMINAL = {"task.completed", "task.failed"}
```

New block:
```python
_STAGE_STATUS = {
    "preprocess":      "preprocessing",
    "transcribe":      "transcribing",
    "verify_segments": "verifying",
    "correct_srt":     "correcting",
    "diarize":         "diarizing",
    "summarize":       "summarizing",
    "detect_chapters": "detecting_chapters",
}

_EVENT_STATUS = {
    "task.submitted": "submitted",
    "task.completed": "completed",
    "task.failed":    "failed",
}

_TERMINAL = {"task.completed", "task.failed"}
```

Replace the status assignment block (old line 33: `new_status = _STATUS_MAP.get(event, "processing")`):

Old (line ~33):
```python
    new_status = _STATUS_MAP.get(event, "processing")
    task_fields: dict = {"status": new_status}
```

New:
```python
    if event == "stage.started":
        new_status = _STAGE_STATUS.get(fields.get("stage", ""), "processing")
    elif event == "stage.completed":
        new_status = None  # keep current status until next stage.started
    else:
        new_status = _EVENT_STATUS.get(event, "processing")

    task_fields: dict = {}
    if new_status is not None:
        task_fields["status"] = new_status
```

Also update the `return` at the end of `process_event()` (currently `return new_status`):
```python
    return new_status if new_status is not None else "processing"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_event_processor_status.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Run existing event processor tests to check for regressions**

```bash
pytest tests/test_event_processor_minio.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/event_processor.py tests/test_event_processor_status.py
git commit -m "feat(api): per-stage status — preprocessing/transcribing/summarizing etc."
```

---

## Task 2: Expand get_status_overview query

**Files:**
- Modify: `api/db.py:90-93`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_event_processor_status.py` (append to end of file):

```python
def test_get_status_overview_includes_transcribing_tasks():
    import importlib
    import api.db as db_mod
    import api.event_processor as ep
    importlib.reload(ep)
    importlib.reload(db_mod)

    # Task enters transcribing state
    _run(ep.process_event(_event("task.submitted", filename="a.m4a")))
    _run(ep.process_event(_event("stage.started", stage="transcribe")))

    overview = _run(db_mod.get_status_overview())
    assert any(t["stem"] == "s1" for t in overview["processing"]), \
        "transcribing task must appear in processing list"


def test_get_status_overview_includes_all_active_stages():
    import importlib
    import api.db as db_mod
    import api.event_processor as ep
    importlib.reload(ep)
    importlib.reload(db_mod)

    active_stages = [
        "preprocess", "transcribe", "verify_segments",
        "correct_srt", "diarize", "summarize", "detect_chapters",
    ]
    for stage in active_stages:
        _run(ep.process_event(_event("stage.started", stem=stage, stage=stage)))

    overview = _run(db_mod.get_status_overview())
    stems_in_processing = {t["stem"] for t in overview["processing"]}
    for stage in active_stages:
        assert stage in stems_in_processing, f"stem={stage} (status={stage[0:4]}…) missing from processing"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_event_processor_status.py::test_get_status_overview_includes_transcribing_tasks tests/test_event_processor_status.py::test_get_status_overview_includes_all_active_stages -v
```

Expected: FAIL — `processing` list is empty because the query only matches `status = 'processing'`.

- [ ] **Step 3: Update get_status_overview in db.py**

In `api/db.py`, replace the processing query (around line 90):

Old:
```python
        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'processing' ORDER BY started_at DESC"
        )
        processing = [dict(r) for r in await cur.fetchall()]
```

New:
```python
        _active = (
            "'processing',"          # legacy rows before this change
            "'preprocessing','transcribing','verifying',"
            "'correcting','diarizing','summarizing','detecting_chapters'"
        )
        cur = await db.execute(
            f"SELECT * FROM tasks WHERE status IN ({_active}) ORDER BY started_at DESC"
        )
        processing = [dict(r) for r in await cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_event_processor_status.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/db.py tests/test_event_processor_status.py
git commit -m "feat(api): expand status overview query to include all active stage statuses"
```

---

## Task 3: Heartbeat log during transcription

**Files:**
- Modify: `pipeline/stages.py:1-10` (imports), `:89-131` (transcribe function)
- Create: `tests/test_stages_heartbeat.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_stages_heartbeat.py`:

```python
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_cfg():
    return {
        "whisper": {
            "service_url": "http://localhost:9001",
            "language": "zh",
            "initial_prompt": "",
        }
    }


def test_heartbeat_thread_starts_and_stops(tmp_path, caplog):
    import logging
    from pipeline.stages import transcribe

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"segments": []}

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    threads_before = set(t.name for t in threading.enumerate())

    with patch("pipeline.stages.httpx.post", return_value=mock_resp), \
         caplog.at_level(logging.INFO, logger="pipeline.stages"):
        transcribe(audio, "s1", tmp_path, _make_cfg())

    # Heartbeat thread is daemon — may already be gone, but it must have existed
    # We verify indirectly: no exception raised and SRT was written
    assert (tmp_path / "s1.srt").exists()


def test_heartbeat_stops_on_exception(tmp_path):
    import httpx as real_httpx
    from pipeline.stages import transcribe

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    with patch("pipeline.stages.httpx.post",
               side_effect=real_httpx.ConnectError("refused")):
        try:
            transcribe(audio, "s1", tmp_path, _make_cfg())
        except RuntimeError:
            pass  # expected

    # Give any daemon threads a moment to observe _stop
    time.sleep(0.1)

    alive_hb = [t for t in threading.enumerate() if t.name == "hb-s1"]
    assert not alive_hb, "heartbeat thread must not outlive the transcribe call"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_stages_heartbeat.py -v
```

Expected: ImportError or AttributeError — `transcribe` has no heartbeat thread yet.

- [ ] **Step 3: Add heartbeat to transcribe() in stages.py**

Add `threading` and `time` to the imports at the top of `pipeline/stages.py`:

Old:
```python
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
```

New:
```python
import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
```

Replace the body of `transcribe()` from line 101 to the end of the try/except block (lines 102–119):

Old:
```python
    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{service_url}/transcribe_segments",
                files={"audio": (audio_path.name, f)},
                params=params,
                timeout=1800.0,
            )
        resp.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach Whisper service at {service_url}. "
            "Start it before running the pipeline."
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Whisper service error {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc
```

New:
```python
    _t0 = time.monotonic()
    _stop = threading.Event()

    def _heartbeat():
        while not _stop.wait(60):
            log.info("transcription in progress: %s (%.0fs elapsed)", stem, time.monotonic() - _t0)

    threading.Thread(target=_heartbeat, daemon=True, name=f"hb-{stem}").start()
    try:
        try:
            with open(audio_path, "rb") as f:
                resp = httpx.post(
                    f"{service_url}/transcribe_segments",
                    files={"audio": (audio_path.name, f)},
                    params=params,
                    timeout=1800.0,
                )
            resp.raise_for_status()
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot reach Whisper service at {service_url}. "
                "Start it before running the pipeline."
            )
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Whisper service error {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
    finally:
        _stop.set()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_stages_heartbeat.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run existing stages tests to check for regressions**

```bash
pytest tests/test_stages.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/stages.py tests/test_stages_heartbeat.py
git commit -m "feat(pipeline): heartbeat log every 60s during transcription"
```

---

## Task 4: PID lock in start-pipeline.sh

**Files:**
- Modify: `scripts/start-pipeline.sh`

No automated test for a shell script guard — verified manually in step 3.

- [ ] **Step 1: Edit start-pipeline.sh**

Replace the entire file content:

Old:
```bash
#!/usr/bin/env bash
# Start pipeline watcher in foreground (useful for debugging).
# For background daemon mode use: scripts/ctl.sh start watcher
set -e
cd "$(dirname "$0")/.."

[[ -f venv/bin/activate ]] || { python3 -m venv venv; }
source venv/bin/activate
pip install -q -r requirements.txt

echo "Starting mediaflow pipeline watcher (foreground)..."
python -m pipeline.watcher
```

New:
```bash
#!/usr/bin/env bash
# Start pipeline watcher in foreground (useful for debugging).
# For background daemon mode use: scripts/ctl.sh start watcher
set -e
cd "$(dirname "$0")/.."

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

[[ -f venv/bin/activate ]] || { python3 -m venv venv; }
source venv/bin/activate
pip install -q -r requirements.txt

echo "Starting mediaflow pipeline watcher (foreground)..."
python -m pipeline.watcher
```

- [ ] **Step 2: Verify PID lock works manually**

```bash
# Run in background, then immediately try again — second run must be rejected
bash scripts/start-pipeline.sh &
BGPID=$!
sleep 1
bash scripts/start-pipeline.sh
# Expected output: "Watcher already running (PID XXXXX). Stop it first: kill XXXXX"
# Expected exit code: 1
kill $BGPID 2>/dev/null; wait $BGPID 2>/dev/null; true
```

- [ ] **Step 3: Verify stale PID file is handled**

```bash
echo "99999999" > /tmp/mediaflow-watcher.pid   # PID that does not exist
bash scripts/start-pipeline.sh &
BGPID=$!
sleep 1
# Should start normally (stale PID ignored)
kill $BGPID 2>/dev/null; wait $BGPID 2>/dev/null; true
rm -f /tmp/mediaflow-watcher.pid
```

- [ ] **Step 4: Commit**

```bash
git add scripts/start-pipeline.sh
git commit -m "feat(scripts): add PID lock to start-pipeline.sh — prevent duplicate watcher processes"
```

---

## Task 5: stop_after_stage config flag

**Files:**
- Modify: `pipeline/runner.py:121` (execute signature + loop body)
- Modify: `pipeline/watcher.py:46` (_run_pipeline body)
- Modify: `config.yaml.example:12` (add commented key)
- Create: `tests/test_runner_stop_after.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner_stop_after.py`:

```python
from unittest.mock import MagicMock, call, patch
from pathlib import Path
import pytest

from pipeline.runner import execute


def _cfg(stop_after=None):
    cfg = {
        "pipeline": {
            "stages": [
                {"id": "preprocess",  "enabled": True},
                {"id": "transcribe",  "enabled": True},
                {"id": "summarize",   "enabled": True},
            ]
        }
    }
    if stop_after:
        cfg["pipeline"]["stop_after_stage"] = stop_after
    return cfg


def _ctx(tmp_path):
    audio = tmp_path / "s1_clean.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)
    srt = tmp_path / "s1.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    return {
        "stem": "s1",
        "workspace": tmp_path,
        "output_dir": tmp_path,
        "input_path": tmp_path / "s1.m4a",
        "audio_path": audio,
        "srt_path": srt,
    }


def test_stop_after_transcribe_skips_summarize(tmp_path):
    pub = MagicMock()
    ctx = _ctx(tmp_path)

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize") as mock_sum:
        execute(_cfg(), ctx, pub, stop_after="transcribe")

    mock_sum.assert_not_called()


def test_stop_after_publishes_completed_event_for_stop_stage(tmp_path):
    pub = MagicMock()
    ctx = _ctx(tmp_path)

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize"):
        execute(_cfg(), ctx, pub, stop_after="transcribe")

    completed_calls = [c for c in pub.publish.call_args_list
                       if c.args[0] == "stage.completed" and c.kwargs.get("stage") == "transcribe"]
    assert completed_calls, "stage.completed/transcribe must be published even when stop_after fires"


def test_no_stop_after_runs_all_stages(tmp_path):
    pub = MagicMock()
    ctx = _ctx(tmp_path)

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize", return_value=tmp_path / "s1_summary.md") as mock_sum:
        execute(_cfg(), ctx, pub, stop_after=None)

    mock_sum.assert_called_once()


def test_stop_after_disabled_stage_runs_to_completion(tmp_path):
    """If stop_after names a disabled stage, all enabled stages run."""
    pub = MagicMock()
    ctx = _ctx(tmp_path)
    cfg = {
        "pipeline": {
            "stages": [
                {"id": "preprocess",      "enabled": True},
                {"id": "correct_srt",     "enabled": False},  # disabled — break never fires
                {"id": "transcribe",      "enabled": True},
                {"id": "summarize",       "enabled": True},
            ]
        }
    }

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize", return_value=tmp_path / "s1_summary.md") as mock_sum:
        execute(cfg, ctx, pub, stop_after="correct_srt")

    mock_sum.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner_stop_after.py -v
```

Expected: `test_stop_after_transcribe_skips_summarize` FAILS because `execute()` has no `stop_after` parameter yet.

- [ ] **Step 3: Add stop_after to runner.execute()**

In `pipeline/runner.py`, change the function signature (line 121):

Old:
```python
def execute(
    cfg: dict,
    ctx: dict,
    pub: EventPublisher,
    from_stage: Optional[str] = None,
) -> dict:
```

New:
```python
def execute(
    cfg: dict,
    ctx: dict,
    pub: EventPublisher,
    from_stage: Optional[str] = None,
    stop_after: Optional[str] = None,
) -> dict:
```

In the stage loop, add the break after `pub.publish("stage.completed", ...)`:

Old:
```python
        pub.publish("stage.completed", ctx["stem"], stage=sid, **extra)
```

New:
```python
        pub.publish("stage.completed", ctx["stem"], stage=sid, **extra)
        if stop_after and sid == stop_after:
            log.info("stop_after_stage=%s reached, halting pipeline", stop_after)
            break
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_runner_stop_after.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Wire stop_after_stage in watcher.py**

In `pipeline/watcher.py`, replace line 46 inside `_run_pipeline()`:

Old:
```python
        ctx = runner.execute(cfg, ctx, pub)
```

New:
```python
        stop_after = cfg.get("pipeline", {}).get("stop_after_stage")
        ctx = runner.execute(cfg, ctx, pub, stop_after=stop_after)
```

- [ ] **Step 6: Add commented key to config.yaml.example**

In `config.yaml.example`, add after `recording_type: auto` line (line 12):

Old:
```yaml
  recording_type: auto             # auto | course | meeting | general
  stages:
```

New:
```yaml
  recording_type: auto             # auto | course | meeting | general
  # stop_after_stage: transcribe   # stop after this stage — skip all later stages (e.g. skip summarize)
  stages:
```

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
pytest -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/runner.py pipeline/watcher.py config.yaml.example tests/test_runner_stop_after.py
git commit -m "feat(pipeline): add stop_after_stage config — halt pipeline after named stage"
```

---

## Self-Review Checklist

- **Spec coverage:** Item A (event_processor + db query) → Tasks 1+2. Item B (heartbeat) → Task 3. Item C (PID lock) → Task 4. Item D (stop_after) → Task 5. ✓
- **Placeholders:** None. All steps contain exact code. ✓
- **Type consistency:** `stop_after: Optional[str]` used in Task 5 step 3 signature matches usage in Tasks 5 step 5 and tests. `new_status: str | None` introduced in Task 1 step 3 — return fallback `"processing"` ensures callers always receive a `str`. ✓
- **Backward compat:** `_active` SQL string includes `'processing'` for rows written before this deploy. ✓
- **Stage.completed return value:** `process_event()` returns `"processing"` when `new_status is None` — HTTP route `{"new_status": ...}` still serializes cleanly. ✓
