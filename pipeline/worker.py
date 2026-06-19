"""Progress Worker — MQ consumer + sequential stage runner + MinIO I/O."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx
import redis

from pipeline import runner
from pipeline.minio_io import PipelineMinIOClient, client_from_env

log = logging.getLogger(__name__)

JOBS_STREAM    = os.getenv("MQ_JOBS_STREAM", "mediaflow:jobs")
CONSUMER_GROUP = os.getenv("MQ_CONSUMER_GROUP", "pipeline-workers")
CONSUMER_NAME  = os.getenv("MQ_CONSUMER_NAME", "worker-1")
DAGSERVICE_URL = os.getenv("DAGSERVICE_URL", "http://localhost:8080")
MAX_CONCURRENT = int(os.getenv("UPLOAD_MAX_CONCURRENT", "2"))
WORKSPACE      = Path(os.getenv("WORKSPACE_DIR", "./workspace"))


def _post_callback(url: str, body: dict) -> None:
    try:
        httpx.post(url, json=body, timeout=10.0)
    except Exception as exc:
        log.error("Callback failed (%s): %s", url, exc)


def _run_stage(
    stage_id: str,
    ctx: dict,
    stage_cfg: dict,
    workspace: Path,
    providers: dict,
) -> dict:
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

    stem = Path(processing_path).stem.replace("_clean", "")

    local_audio = job_workspace / Path(processing_path).name
    log.info("Downloading %s → %s", processing_path, local_audio)
    minio.download_processing_file(processing_path, local_audio)

    ctx: dict = {
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
                "job_id":        job_id,
                "stage":         stage_id,
                "status":        "success",
                "retry_attempt": retry_attempt,
                "error_msg":     None,
            })
        except Exception as exc:
            log.error("Job %s stage %s failed: %s", job_id, stage_id, exc)
            _post_callback(f"{dagservice_url}/internal/stage-callback", {
                "job_id":        job_id,
                "stage":         stage_id,
                "status":        "failed",
                "retry_attempt": retry_attempt,
                "error_msg":     str(exc),
            })
            return  # stop processing — DAG-Service handles retry

    log.info("Job %s complete — uploading outputs", job_id)
    minio.upload_job_outputs(job_id, stem, output_dir)
    shutil.rmtree(job_workspace, ignore_errors=True)


def _consumer_loop(
    r: redis.Redis,
    minio: PipelineMinIOClient,
    workspace: Path,
    dagservice_url: str,
) -> None:
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
    minio = client_from_env()
    log.info("Progress Worker starting (max_concurrent=%d)", MAX_CONCURRENT)
    _consumer_loop(r, minio, WORKSPACE, DAGSERVICE_URL)


if __name__ == "__main__":
    main()
