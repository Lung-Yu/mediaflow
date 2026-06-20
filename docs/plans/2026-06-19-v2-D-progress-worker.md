# Sub-plan D: Progress Worker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `pipeline/watcher.py` + `api/mq/queue_consumer.py` with a single `pipeline/worker.py` that: consumes `mediaflow:jobs` MQ messages, downloads the source file from MinIO `processing/`, runs stages sequentially using provider instances, uploads outputs to MinIO `output/`, and reports each stage result to the DAG-Service via HTTP POST.

**Architecture:** XREADGROUP consumer loop in a thread (not asyncio — pipeline stages are blocking). One `ThreadPoolExecutor` worker per concurrent job slot. Stage runner calls `_build_providers_for_stage()` from Sub-plan B. MinIO I/O via `pipeline/minio_io.py` (new thin wrapper around the existing `api/minio_client.MinIOClient`). 

**Tech Stack:** redis-py (sync), boto3 (sync), threading, httpx (sync for stage-callback POST)

**Depends on:** Sub-plan A (jobs table), Sub-plan B (providers), Sub-plan C (DAG-Service + MQ schema)

## Global Constraints

- Worker uses **synchronous** Redis and boto3 — no asyncio in pipeline code
- XACK immediately on receive; retry is the DAG-Service's responsibility
- Stage outputs are overwritten on retry (idempotent upload to MinIO `output/`)
- Working directory for stages: a tmp dir per job under `workspace/2_processing/{job_id}/`; cleaned up after upload
- Stage-callback HTTP POST target: `DAGSERVICE_URL` env var (default `http://localhost:8080`)
- `resume_from_stage` causes the worker to skip all earlier stages in the plan
- max_concurrent_jobs: `UPLOAD_MAX_CONCURRENT` env var (default 2) — ThreadPoolExecutor size

---

## File Structure

```
# Create
pipeline/worker.py         — MQ consumer + stage runner + MinIO I/O + HTTP callback
pipeline/minio_io.py       — sync MinIO helpers for pipeline (download/upload)

# Modify
pipeline/runner.py         — accept stage_plan list from MQ message (not _DEFAULT_STAGES)
scripts/start-pipeline.sh  — launch worker.py instead of watcher.py (or in addition to)

# Keep (not deleted yet — some installations use local-folder mode)
pipeline/watcher.py        — unchanged; legacy local-folder mode

# Test
tests/test_worker.py       — unit tests (mock MQ, mock MinIO, mock stage runner, mock HTTP)
tests/test_minio_io.py     — MinIO I/O helper tests (mock boto3)
```

---

## Interfaces

**Consumes:**
```python
# MQ message from mediaflow:jobs (XREADGROUP)
{
    "job_id":            "abc123",
    "processing_path":   "processing/abc123/lesson01.wav",
    "stage_plan":        '[{"stage":"preprocess","config":{...}}]',  # JSON string
    "retry_attempt":     "0",
    "resume_from_stage": "preprocess"
}

# provider factory (Sub-plan B)
from pipeline.runner import _build_providers_for_stage
providers = _build_providers_for_stage(stage_def)

# stage functions (Sub-plan B modified stages.py)
from pipeline.stages import preprocess, transcribe, summarize, diarize, ...
```

**Produces:**
```python
# HTTP POST to DAG-Service after each stage
POST {DAGSERVICE_URL}/internal/stage-callback
{
    "job_id":        "abc123",
    "stage":         "transcribe",
    "status":        "success",   # | "failed"
    "retry_attempt": 0,
    "error_msg":     null
}

# MinIO uploads on completion
output/{job_id}/lesson01.srt
output/{job_id}/lesson01_summary.md
output/{job_id}/lesson01_summary.json
output/{job_id}/lesson01_clean.wav  (kept forever; source for clip API)
```

---

## Task 1: MinIO I/O Helpers for Pipeline

**Files:**
- Create: `pipeline/minio_io.py`
- Create: `tests/test_minio_io.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_minio_io.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

def make_client():
    from pipeline.minio_io import PipelineMinIOClient
    return PipelineMinIOClient(
        endpoint="localhost:9000",
        access_key="mediaflow",
        secret_key="changeme",
        secure=False,
        processing_bucket="mediaflow-processing",
        output_bucket="mediaflow-output",
    )

def test_download_processing_file(tmp_path):
    client = make_client()
    dest = tmp_path / "audio.wav"
    with patch.object(client._s3, "download_file") as mock_dl:
        client.download_processing_file("processing/job1/audio.wav", dest)
    mock_dl.assert_called_once_with(
        "mediaflow-processing", "processing/job1/audio.wav", str(dest)
    )
    assert dest.parent.exists()

def test_upload_output_file(tmp_path):
    client = make_client()
    f = tmp_path / "test.srt"
    f.write_text("srt content")
    with patch.object(client._s3, "upload_file") as mock_ul:
        client.upload_output("job1", f, key_suffix="test.srt")
    mock_ul.assert_called_once_with(
        Filename=str(f),
        Bucket="mediaflow-output",
        Key="output/job1/test.srt",
    )

def test_upload_job_outputs_uploads_all_existing_files(tmp_path):
    client = make_client()
    (tmp_path / "stem.srt").write_text("srt")
    (tmp_path / "stem_summary.md").write_text("md")
    (tmp_path / "stem_summary.json").write_text("{}")
    (tmp_path / "stem_clean.wav").write_bytes(b"wav")
    # stem_chapters.json does NOT exist — should be skipped without error
    with patch.object(client._s3, "upload_file") as mock_ul:
        client.upload_job_outputs("job1", "stem", tmp_path)
    assert mock_ul.call_count == 4  # srt, md, json, wav (not chapters.json)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_minio_io.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.minio_io'`

- [ ] **Step 3: Write pipeline/minio_io.py**

```python
# pipeline/minio_io.py
"""Synchronous MinIO helpers for the pipeline worker."""
import os
from pathlib import Path
import boto3
from botocore.config import Config

_OUTPUT_SUFFIXES = [".srt", "_summary.md", "_summary.json", "_chapters.json", "_clean.wav"]


class PipelineMinIOClient:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        processing_bucket: str,
        output_bucket: str,
    ):
        scheme = "https" if secure else "http"
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )
        self.processing_bucket = processing_bucket
        self.output_bucket = output_bucket

    def download_processing_file(self, processing_key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.processing_bucket, processing_key, str(dest))

    def upload_output(self, job_id: str, local_path: Path, key_suffix: str) -> None:
        self._s3.upload_file(
            Filename=str(local_path),
            Bucket=self.output_bucket,
            Key=f"output/{job_id}/{key_suffix}",
        )

    def upload_job_outputs(self, job_id: str, stem: str, output_dir: Path) -> None:
        for suffix in _OUTPUT_SUFFIXES:
            path = output_dir / f"{stem}{suffix}"
            if path.exists():
                self.upload_output(job_id, path, key_suffix=f"{stem}{suffix}")


def client_from_env() -> PipelineMinIOClient:
    return PipelineMinIOClient(
        endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "mediaflow"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "changeme"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        processing_bucket=os.getenv("MINIO_PROCESSING_BUCKET", "mediaflow-processing"),
        output_bucket=os.getenv("MINIO_OUTPUT_BUCKET", "mediaflow-output"),
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_minio_io.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/minio_io.py tests/test_minio_io.py
git commit -m "feat(pipeline): PipelineMinIOClient — sync MinIO helpers for worker"
```

---

## Task 2: Progress Worker Core

**Files:**
- Create: `pipeline/worker.py`
- Create: `tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_worker.py
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

STAGE_PLAN = json.dumps([
    {"stage": "preprocess",  "config": {"provider": "ffmpeg"}},
    {"stage": "transcribe",  "config": {"provider": "mlx-whisper", "language": "zh"}},
    {"stage": "summarize",   "config": {"provider": "ollama", "model": "qwen2.5:7b"}},
])


def make_mq_message(resume_from="preprocess", retry=0):
    return {
        "job_id":            "job1",
        "processing_path":   "processing/job1/test.wav",
        "stage_plan":        STAGE_PLAN,
        "retry_attempt":     str(retry),
        "resume_from_stage": resume_from,
    }


def test_worker_runs_all_stages_from_start(tmp_path):
    from pipeline.worker import _process_job
    minio = MagicMock()
    minio.download_processing_file = MagicMock()
    minio.upload_job_outputs = MagicMock()

    stage_results = {"preprocess": tmp_path / "test_clean.wav",
                     "transcribe": tmp_path / "test.srt",
                     "summarize":  (tmp_path / "test_summary.md", tmp_path / "test_summary.json")}

    http_calls = []

    with patch("pipeline.worker._run_stage", side_effect=lambda stage, ctx, cfg, ws, prov: (
        {**ctx, "audio_path": stage_results["preprocess"]} if stage == "preprocess"
        else {**ctx, "srt_path": stage_results["transcribe"]} if stage == "transcribe"
        else {**ctx, "summary_md": stage_results["summarize"][0]}
    )) as mock_run, \
    patch("pipeline.worker._post_callback", side_effect=lambda url, body: http_calls.append(body)):
        msg = make_mq_message()
        _process_job(msg, minio=minio, workspace=tmp_path, dagservice_url="http://localhost:8080")

    assert mock_run.call_count == 3
    assert len(http_calls) == 3
    assert all(c["status"] == "success" for c in http_calls)
    assert minio.upload_job_outputs.called


def test_worker_resumes_from_stage(tmp_path):
    from pipeline.worker import _process_job
    minio = MagicMock()
    minio.download_processing_file = MagicMock()
    minio.upload_job_outputs = MagicMock()
    ran_stages = []

    with patch("pipeline.worker._run_stage", side_effect=lambda stage, ctx, cfg, ws, prov: (
        ran_stages.append(stage) or ctx
    )), patch("pipeline.worker._post_callback"):
        msg = make_mq_message(resume_from="transcribe")
        _process_job(msg, minio=minio, workspace=tmp_path, dagservice_url="http://localhost:8080")

    assert "preprocess" not in ran_stages  # skipped
    assert "transcribe" in ran_stages
    assert "summarize" in ran_stages


def test_worker_reports_failure_on_stage_error(tmp_path):
    from pipeline.worker import _process_job
    minio = MagicMock()
    minio.download_processing_file = MagicMock()
    http_calls = []

    with patch("pipeline.worker._run_stage", side_effect=RuntimeError("ffmpeg error")), \
         patch("pipeline.worker._post_callback", side_effect=lambda url, body: http_calls.append(body)):
        msg = make_mq_message()
        _process_job(msg, minio=minio, workspace=tmp_path, dagservice_url="http://localhost:8080")

    assert http_calls[-1]["status"] == "failed"
    assert "ffmpeg error" in http_calls[-1]["error_msg"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_worker.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.worker'`

- [ ] **Step 3: Write pipeline/worker.py**

```python
# pipeline/worker.py
"""Progress Worker — MQ consumer + sequential stage runner + MinIO I/O."""
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import redis

from pipeline import runner, stages
from pipeline.minio_io import PipelineMinIOClient, client_from_env

log = logging.getLogger(__name__)

JOBS_STREAM      = os.getenv("MQ_JOBS_STREAM", "mediaflow:jobs")
CONSUMER_GROUP   = os.getenv("MQ_CONSUMER_GROUP", "pipeline-workers")
CONSUMER_NAME    = os.getenv("MQ_CONSUMER_NAME", "worker-1")
DAGSERVICE_URL   = os.getenv("DAGSERVICE_URL", "http://localhost:8080")
MAX_CONCURRENT   = int(os.getenv("UPLOAD_MAX_CONCURRENT", "2"))
WORKSPACE        = Path(os.getenv("WORKSPACE_DIR", "./workspace"))


def _post_callback(url: str, body: dict) -> None:
    try:
        httpx.post(url, json=body, timeout=10.0)
    except Exception as exc:
        log.error("Callback failed (%s): %s", url, exc)


def _run_stage(stage_id: str, ctx: dict, stage_cfg: dict,
               workspace: Path, providers: dict) -> dict:
    """Run one stage and return updated ctx. Raises on failure."""
    adapter = runner._STAGE_ADAPTERS.get(stage_id)
    if not adapter:
        raise ValueError(f"Unknown stage: {stage_id!r}")
    new_ctx, _ = adapter(ctx, {**stage_cfg, **providers})
    return new_ctx


def _process_job(
    msg: dict,
    minio: PipelineMinIOClient,
    workspace: Path,
    dagservice_url: str,
) -> None:
    job_id            = msg["job_id"]
    processing_path   = msg["processing_path"]
    stage_plan        = json.loads(msg["stage_plan"])
    retry_attempt     = int(msg.get("retry_attempt", "0"))
    resume_from_stage = msg.get("resume_from_stage", stage_plan[0]["stage"])

    job_workspace = workspace / "2_processing" / job_id
    job_workspace.mkdir(parents=True, exist_ok=True)
    output_dir = workspace / "3_output" / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Derive stem from processing_path filename
    stem = Path(processing_path).stem.replace("_clean", "")

    # Download source file from MinIO processing/
    local_audio = job_workspace / Path(processing_path).name
    log.info("Downloading %s → %s", processing_path, local_audio)
    minio.download_processing_file(processing_path, local_audio)

    ctx = {
        "stem":       stem,
        "job_id":     job_id,
        "workspace":  workspace,
        "output_dir": output_dir,
        "input_path": local_audio,
        "audio_path": local_audio,
    }

    skipping = True
    for stage_def in stage_plan:
        stage_id  = stage_def["stage"]
        stage_cfg = stage_def.get("config", {})

        if skipping:
            if stage_id == resume_from_stage:
                skipping = False
            else:
                log.debug("Skipping stage %s (resume_from=%s)", stage_id, resume_from_stage)
                continue

        log.info("Job %s: running stage %s (attempt %d)", job_id, stage_id, retry_attempt)
        try:
            providers = runner._build_providers_for_stage(stage_def)
            ctx = _run_stage(stage_id, ctx, stage_cfg, workspace, providers)
            _post_callback(f"{dagservice_url}/internal/stage-callback", {
                "job_id": job_id, "stage": stage_id,
                "status": "success", "retry_attempt": retry_attempt, "error_msg": None,
            })
        except Exception as exc:
            log.error("Job %s stage %s failed: %s", job_id, stage_id, exc)
            _post_callback(f"{dagservice_url}/internal/stage-callback", {
                "job_id": job_id, "stage": stage_id,
                "status": "failed", "retry_attempt": retry_attempt, "error_msg": str(exc),
            })
            return  # stop processing — DAG-Service handles retry

    # All stages complete — upload outputs
    log.info("Job %s complete — uploading outputs", job_id)
    minio.upload_job_outputs(job_id, stem, output_dir)
    shutil.rmtree(job_workspace, ignore_errors=True)


def _consumer_loop(r: redis.Redis, minio: PipelineMinIOClient,
                   workspace: Path, dagservice_url: str) -> None:
    try:
        r.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass  # group already exists

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
        while True:
            messages = r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {JOBS_STREAM: ">"}, count=1, block=5000,
            )
            if not messages:
                continue
            for _stream, entries in messages:
                for entry_id, fields in entries:
                    r.xack(JOBS_STREAM, CONSUMER_GROUP, entry_id)
                    pool.submit(_process_job, fields, minio, workspace, dagservice_url)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
    minio = client_from_env()
    log.info("Progress Worker starting (max_concurrent=%d)", MAX_CONCURRENT)
    _consumer_loop(r, minio, WORKSPACE, DAGSERVICE_URL)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_worker.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/worker.py tests/test_worker.py
git commit -m "feat(pipeline): Progress Worker — MQ consumer + sequential stage runner + MinIO I/O"
```

---

## Task 3: Add `mediaflow-processing` Bucket to Docker Compose

**Files:**
- Modify: `docker-compose.yml`
- Modify: `api/minio_client.py`

- [ ] **Step 1: Add MINIO_PROCESSING_BUCKET env var to api service**

```yaml
      - MINIO_PROCESSING_BUCKET=${MINIO_PROCESSING_BUCKET:-mediaflow-processing}
```

- [ ] **Step 2: Add processing bucket creation to MinIOClient.ensure_buckets()**

In `api/minio_client.py`, `ensure_buckets()` currently creates `input_bucket` and `output_bucket`. Add:

```python
PROCESSING_BUCKET = os.getenv("MINIO_PROCESSING_BUCKET", "mediaflow-processing")
CLIPS_BUCKET      = os.getenv("MINIO_CLIPS_BUCKET", "mediaflow-clips")
```

Update `ensure_buckets()` to also create `processing_bucket` and `clips_bucket`.

Add `set_bucket_lifecycle` calls for:
- `processing_bucket`: TTL 7 days
- `clips_bucket`: TTL 1 day (conservative; 10min is too short for lifecycle policies)

- [ ] **Step 3: Add copy_input_to_processing() to MinIOClient**

```python
def copy_input_to_processing(self, input_key: str, job_id: str) -> str:
    """Copy file from input/ to processing/{job_id}/.
    Returns the processing key."""
    filename = input_key.split("/")[-1]
    processing_key = f"processing/{job_id}/{filename}"
    self._s3.copy_object(
        CopySource={"Bucket": self.input_bucket, "Key": input_key},
        Bucket=self.processing_bucket,
        Key=processing_key,
    )
    return processing_key
```

- [ ] **Step 4: Test copy helper**

```python
# tests/test_minio_client.py — add
def test_copy_input_to_processing():
    from api.minio_client import MinIOClient
    client = MinIOClient("ep", "ak", "sk", False,
                         "mediaflow-input", "mediaflow-output")
    client.processing_bucket = "mediaflow-processing"
    with patch.object(client._s3, "copy_object") as mock_cp:
        key = client.copy_input_to_processing("input/test.m4a", "job1")
    assert key == "processing/job1/test.m4a"
    mock_cp.assert_called_once()
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_minio_client.py tests/test_worker.py tests/test_minio_io.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml api/minio_client.py tests/test_minio_client.py
git commit -m "feat(storage): add processing + clips MinIO buckets; copy_input_to_processing helper"
```

---

## Task 4: Update start-pipeline.sh to Launch Worker

**Files:**
- Modify: `scripts/start-pipeline.sh`

- [ ] **Step 1: Add worker launch option**

In `scripts/start-pipeline.sh`, add a MODE check:

```bash
MODE="${PIPELINE_MODE:-worker}"  # worker | watcher | both

case "$MODE" in
  worker)
    echo "Starting Progress Worker (MQ mode)..."
    exec python -m pipeline.worker
    ;;
  watcher)
    echo "Starting folder watcher (legacy local mode)..."
    exec python -m pipeline.watcher
    ;;
  both)
    python -m pipeline.worker &
    exec python -m pipeline.watcher
    ;;
esac
```

- [ ] **Step 2: Test manual launch**

```bash
PIPELINE_MODE=worker \
REDIS_HOST=localhost \
MINIO_ENDPOINT=localhost:9000 \
DAGSERVICE_URL=http://localhost:8080 \
python -m pipeline.worker &

# Should log: "Progress Worker starting (max_concurrent=2)"
# Then block waiting for MQ messages
kill %1
```

- [ ] **Step 3: Commit**

```bash
git add scripts/start-pipeline.sh
git commit -m "feat(scripts): PIPELINE_MODE=worker launches Progress Worker"
```

---

## Self-Review Checklist

- [ ] `_process_job` with `resume_from_stage="transcribe"` skips `preprocess` and starts at `transcribe`
- [ ] Stage failure in any stage stops execution and posts `status="failed"` callback — no subsequent stages run
- [ ] `upload_job_outputs` is called only on full success — not after a failed stage
- [ ] `xack` is called before `_process_job` runs — MQ doesn't redeliver on crash (DAG-Service owns retry)
- [ ] `job_workspace` is cleaned up with `shutil.rmtree` after successful upload
- [ ] `ThreadPoolExecutor(max_workers=MAX_CONCURRENT)` limits in-flight jobs to config value
- [ ] `copy_input_to_processing()` copies `input/{key}` → `processing/{job_id}/{filename}` in MinIO — no local download
