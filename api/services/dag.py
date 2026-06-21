"""DAG-Service — job orchestration: trigger, stage callback, retry."""
from __future__ import annotations
import json
import logging
import os
import threading
import time

import asyncpg

from api.db.queries import get_dag_flow, insert_event, upsert_job, get_job

log = logging.getLogger(__name__)

_MQ_KEY = "mediaflow:jobs"
_MAX_RETRIES = int(os.getenv("PIPELINE_MAX_RETRIES", "3"))
_RETRY_BACKOFF = int(os.getenv("PIPELINE_RETRY_BACKOFF_SEC", "30"))


async def trigger_job(
    pool: asyncpg.Pool,
    redis,
    job_id: str,
    filename: str,
    minio_processing_key: str,
    dag_flow_id: str | None,
) -> None:
    flow = await get_dag_flow(pool, dag_flow_id)
    stage_ids = [s["stage"] for s in flow["stage_plan"]]
    await upsert_job(
        pool, job_id,
        filename=filename,
        dag_flow_id=flow["id"],
        status="queued",
        minio_processing_key=minio_processing_key,
        submitted_at=time.time(),
    )
    await _xadd(redis, job_id, minio_processing_key, flow["stage_plan"], 0, stage_ids[0])


async def handle_stage_callback(
    pool: asyncpg.Pool,
    redis,
    job_id: str,
    stage: str,
    status: str,
    retry_attempt: int,
    error_msg: str | None,
) -> None:
    await insert_event(pool, job_id, stage, status,
                       retry_attempt=retry_attempt, error_msg=error_msg)

    if status == "failed":
        if retry_attempt < _MAX_RETRIES:
            job = await get_job(pool, job_id)
            flow = await get_dag_flow(pool, job["dag_flow_id"])
            await upsert_job(pool, job_id,
                             status="queued",
                             retry_count=retry_attempt + 1,
                             current_stage=stage)
            _enqueue_after_backoff(
                redis, job_id,
                job["minio_processing_key"],
                flow["stage_plan"],
                retry_attempt + 1,
                stage,
            )
        else:
            await upsert_job(pool, job_id,
                             status="failed",
                             error_msg=error_msg,
                             completed_at=time.time())
        return

    # success — update current stage; mark completed if last stage
    job = await get_job(pool, job_id)
    flow = await get_dag_flow(pool, job["dag_flow_id"])
    stage_ids = [s["stage"] for s in flow["stage_plan"]]
    await upsert_job(pool, job_id, current_stage=stage, status="processing")
    if stage == stage_ids[-1]:
        await upsert_job(pool, job_id, status="completed", completed_at=time.time())
        log.info("Job %s completed", job_id)


async def _xadd(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from):
    await redis.xadd(_MQ_KEY, {
        "job_id": job_id,
        "processing_path": processing_path,
        "stage_plan": json.dumps(stage_plan),
        "retry_attempt": str(retry_attempt),
        "resume_from_stage": resume_from,
    })


def _enqueue_after_backoff(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from):
    """Re-enqueue after backoff delay. Runs in a daemon thread."""
    import asyncio

    def _run():
        time.sleep(_RETRY_BACKOFF)
        asyncio.run(_xadd(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from))
        log.info("Re-enqueued job %s (attempt %d, from %s)", job_id, retry_attempt, resume_from)

    threading.Thread(target=_run, daemon=True).start()
