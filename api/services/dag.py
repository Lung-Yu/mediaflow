"""DAG-Service — job orchestration: trigger, stage callback, retry, watchdog."""
from __future__ import annotations
import json
import logging
import os
import threading
import time

import asyncpg

from api.db.queries import count_active_jobs, get_dag_flow, insert_event, upsert_job, get_job

log = logging.getLogger(__name__)

_MQ_KEY = "mediaflow:jobs"
_MAX_RETRIES = int(os.getenv("PIPELINE_MAX_RETRIES", "3"))
_RETRY_BACKOFF = int(os.getenv("PIPELINE_RETRY_BACKOFF_SEC", "30"))
_MAX_CONCURRENT = int(os.getenv("PIPELINE_MAX_CONCURRENT_JOBS", "2"))
_JOB_TIMEOUT_SEC = int(os.getenv("PIPELINE_JOB_TIMEOUT_SEC", str(60 * 60)))  # 1 hour


class CapacityError(RuntimeError):
    """Raised when in-flight jobs are at the configured limit."""


async def check_capacity(pool: asyncpg.Pool) -> None:
    """Raise CapacityError if at or over max_concurrent_jobs."""
    active = await count_active_jobs(pool)
    if active >= _MAX_CONCURRENT:
        raise CapacityError(f"Max concurrent jobs ({_MAX_CONCURRENT}) reached ({active} in flight)")


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
    now = time.time()
    await upsert_job(
        pool, job_id,
        filename=filename,
        dag_flow_id=flow["id"],
        status="queued",
        minio_processing_key=minio_processing_key,
        submitted_at=now,
        started_at=now,  # watchdog baseline: reset each time we dispatch to MQ
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
                pool, redis, job_id,
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
    # reset started_at so watchdog timeout counts from last successful stage, not job start
    await upsert_job(pool, job_id, current_stage=stage, status="processing",
                     started_at=time.time())
    if stage == stage_ids[-1]:
        await upsert_job(pool, job_id, status="completed", completed_at=time.time())
        log.info("Job %s completed", job_id)


async def recover_stuck_jobs(pool: asyncpg.Pool, redis) -> None:
    """Detect jobs that stopped reporting past timeout and re-enqueue as retries.

    Compensates for immediate-ack in worker: if worker crashes, no callback arrives
    and this watchdog re-enqueues the job after _JOB_TIMEOUT_SEC seconds.
    """
    cutoff = time.time() - _JOB_TIMEOUT_SEC
    rows = await pool.fetch(
        """SELECT * FROM jobs
           WHERE status IN ('queued', 'processing')
             AND (started_at IS NULL OR started_at < $1)""",
        cutoff,
    )
    for row in rows:
        job = dict(row)
        retry_count = job.get("retry_count") or 0
        log.warning("Watchdog: stuck job %s (status=%s retry=%d)",
                    job["id"], job["status"], retry_count)
        if retry_count < _MAX_RETRIES:
            flow = await get_dag_flow(pool, job.get("dag_flow_id"))
            # Always resume from first stage: worker ctx only has input_path, not
            # intermediate outputs (audio_path, srt_path) that mid-pipeline stages need.
            resume = flow["stage_plan"][0]["stage"]
            await insert_event(pool, job["id"], resume, "failed",
                               retry_attempt=retry_count,
                               error_msg="watchdog: job timeout, worker did not respond")
            await upsert_job(pool, job["id"], status="queued", retry_count=retry_count + 1)
            _enqueue_after_backoff(pool, redis, job["id"], job["minio_processing_key"],
                                   flow["stage_plan"], retry_count + 1, resume)
        else:
            await upsert_job(pool, job["id"],
                             status="failed",
                             error_msg="watchdog: job timeout after max retries",
                             completed_at=time.time())


async def _xadd(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from):
    await redis.xadd(_MQ_KEY, {
        "job_id": job_id,
        "processing_path": processing_path,
        "stage_plan": json.dumps(stage_plan),
        "retry_attempt": str(retry_attempt),
        "resume_from_stage": resume_from,
    })


def _enqueue_after_backoff(pool, redis, job_id, processing_path, stage_plan, retry_attempt, resume_from):
    """Re-enqueue after backoff delay. Runs in a daemon thread."""
    import asyncio

    async def _run():
        await _xadd(redis, job_id, processing_path, stage_plan, retry_attempt, resume_from)
        await upsert_job(pool, job_id, started_at=time.time())  # reset watchdog baseline
        log.info("Re-enqueued job %s (attempt %d, from %s)", job_id, retry_attempt, resume_from)

    def _thread():
        time.sleep(_RETRY_BACKOFF)
        asyncio.run(_run())

    threading.Thread(target=_thread, daemon=True).start()
