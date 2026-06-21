"""Progress Worker — reads MQ, runs pipeline stages, reports to DAG-Service."""
from __future__ import annotations
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import redis as redis_lib

from pipeline.config import load as load_config
from pipeline.runner import execute as run_stages

log = logging.getLogger(__name__)

_MQ_KEY = "mediaflow:jobs"
_CONSUMER_GROUP = "pipeline-workers"
_CONSUMER_NAME = f"worker-{os.getpid()}"
_DAGSERVICE_URL = os.getenv("DAGSERVICE_URL", "http://localhost:8080")


class _CallbackPub:
    """Publishes stage results to DAG-Service via HTTP POST."""

    def __init__(self, job_id: str, retry_attempt: int):
        self.job_id = job_id
        self.retry_attempt = retry_attempt
        self._last_stage: str | None = None

    def publish(self, event: str, stem: str, **kwargs):
        stage = kwargs.get("stage", "")
        if event == "stage.started":
            self._last_stage = stage
            return
        if event == "stage.completed":
            self._post("success", stage, None)

    def report_failure(self, stage: str, error_msg: str):
        self._post("failed", stage, error_msg)

    def _post(self, status: str, stage: str, error_msg: str | None):
        try:
            httpx.post(
                f"{_DAGSERVICE_URL}/internal/stage-callback",
                json={
                    "job_id": self.job_id,
                    "stage": stage,
                    "status": status,
                    "retry_attempt": self.retry_attempt,
                    "error_msg": error_msg,
                },
                timeout=10.0,
            )
        except Exception as exc:
            log.error("Callback failed job=%s stage=%s: %s", self.job_id, stage, exc)


def _download_source(client, processing_path: str, dest_dir: Path) -> Path:
    """Download the source audio file from MinIO processing/ to dest_dir."""
    # processing_path is like "processing/{job_id}/{filename}"
    parts = processing_path.split("/")
    filename = parts[-1] if len(parts) > 1 else processing_path
    dest = dest_dir / filename
    client.download_file_from(processing_path, dest, bucket=client.processing_bucket)
    return dest


def _upload_outputs(client, job_id: str, output_dir: Path):
    """Upload all files from output_dir to MinIO output/{job_id}/."""
    for f in output_dir.iterdir():
        if f.is_file():
            key = f"output/{job_id}/{f.name}"
            client.upload_file(key, f, bucket=client.output_bucket)
            log.info("Uploaded %s → %s", f.name, key)


def _run_job(msg_id: str, fields: dict, r: redis_lib.Redis):
    from api.utils.minio import get_client  # ponytail: reuse existing singleton
    cfg = load_config()
    client = get_client()

    job_id = fields["job_id"]
    processing_path = fields["processing_path"]
    stage_plan = json.loads(fields["stage_plan"])
    retry_attempt = int(fields.get("retry_attempt", "0"))
    resume_from = fields.get("resume_from_stage")

    log.info("Worker: job=%s retry=%d from=%s", job_id, retry_attempt, resume_from)

    # Build cfg with only the planned stages enabled (in order)
    stage_cfgs = [{"id": s["stage"], "enabled": True, **s.get("config", {})}
                  for s in stage_plan]
    job_cfg = {**cfg, "pipeline": {**cfg.get("pipeline", {}), "stages": stage_cfgs}}

    workdir = Path(tempfile.mkdtemp(prefix=f"mf_{job_id}_"))
    output_dir = workdir / "output"
    output_dir.mkdir()

    pub = _CallbackPub(job_id, retry_attempt)

    try:
        audio_path = _download_source(client, processing_path, workdir)
        ctx = {
            "stem": job_id,
            "workspace": workdir,
            "output_dir": output_dir,
            "input_path": audio_path,
        }
        run_stages(job_cfg, ctx, pub, from_stage=resume_from)
        _upload_outputs(client, job_id, output_dir)
    except Exception as exc:
        failed_stage = pub._last_stage or resume_from or stage_plan[0]["stage"]
        log.error("Job %s failed at %s: %s", job_id, failed_stage, exc)
        pub.report_failure(failed_stage, str(exc))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        r.xack(_MQ_KEY, _CONSUMER_GROUP, msg_id)


def _ensure_consumer_group(r: redis_lib.Redis):
    try:
        r.xgroup_create(_MQ_KEY, _CONSUMER_GROUP, id="0", mkstream=True)
    except redis_lib.exceptions.ResponseError:
        pass  # group already exists


def run():
    cfg = load_config()
    max_workers = cfg.get("pipeline", {}).get("max_concurrent_jobs", 2)
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))

    r = redis_lib.Redis(host=redis_host, port=redis_port, decode_responses=True)
    _ensure_consumer_group(r)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    log.info("Progress Worker started (max_workers=%d)", max_workers)

    while True:
        try:
            msgs = r.xreadgroup(
                _CONSUMER_GROUP, _CONSUMER_NAME,
                {_MQ_KEY: ">"},
                count=1, block=5000,
            )
            if msgs:
                for _stream, entries in msgs:
                    for msg_id, fields in entries:
                        executor.submit(_run_job, msg_id, fields, r)
        except KeyboardInterrupt:
            log.info("Worker stopping")
            break
        except Exception as exc:
            log.error("Worker loop error: %s", exc)
            time.sleep(1)


def _demo():
    """ponytail: self-check — no external deps needed."""
    pub = _CallbackPub("job_test", 0)
    pub.publish("stage.started", "job_test", stage="transcribe")
    assert pub._last_stage == "transcribe"
    calls = []
    pub._post = lambda status, stage, err: calls.append((status, stage))
    pub.report_failure("transcribe", "OOM")
    assert calls == [("failed", "transcribe")]
    print("worker self-check: OK")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if "--demo" in sys.argv:
        _demo()
    else:
        run()
