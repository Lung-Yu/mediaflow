"""DAG orchestration — trigger jobs, handle stage callbacks, own retry logic."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import asyncpg
import redis.asyncio as aioredis

from api import db
from api.db.queries import insert_event as _insert_event

log = logging.getLogger(__name__)

JOBS_STREAM = os.getenv("MQ_JOBS_STREAM", "mediaflow:jobs")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF = int(os.getenv("RETRY_BACKOFF_SEC", "30"))


async def trigger_job(
    pool: asyncpg.Pool,
    redis: aioredis.Redis,
    job_id: str,
    flow_id: Optional[str],
    minio_processing_key: str,
    retry_attempt: int = 0,
    resume_from_stage: Optional[str] = None,
) -> None:
    """Look up dag_flow, set status=queued, enqueue to MQ."""
    flow = await db.get_dag_flow(pool, flow_id)
    stage_plan = flow["stage_plan"]
    if isinstance(stage_plan, list):
        stage_plan_json = json.dumps(stage_plan)
    else:
        stage_plan_json = stage_plan

    parsed = stage_plan if isinstance(stage_plan, list) else json.loads(stage_plan_json)
    first_stage = parsed[0]["stage"]
    resume = resume_from_stage or first_stage

    await db.upsert_job(pool, job_id,
                        status="queued",
                        dag_flow_id=flow["id"],
                        current_stage=resume,
                        minio_processing_key=minio_processing_key)

    await redis.xadd(JOBS_STREAM, {
        "job_id":            job_id,
        "processing_path":   minio_processing_key,
        "stage_plan":        stage_plan_json,
        "retry_attempt":     str(retry_attempt),
        "resume_from_stage": resume,
    })
    log.info("Enqueued job %s (flow=%s, resume=%s, attempt=%d)",
             job_id, flow["id"], resume, retry_attempt)


async def handle_stage_callback(
    pool: asyncpg.Pool,
    redis: aioredis.Redis,
    job_id: str,
    stage: str,
    status: str,
    retry_attempt: int = 0,
    error_msg: Optional[str] = None,
) -> None:
    """Called when Progress Worker reports a stage result."""
    await _insert_event(pool, job_id, stage=stage, status=status,
                        retry_attempt=retry_attempt, error_msg=error_msg,
                        ts=time.time())

    if status == "success":
        await db.upsert_job(pool, job_id, current_stage=stage, status="processing")
        log.info("Stage %s succeeded for job %s", stage, job_id)
        return

    if retry_attempt < MAX_RETRIES:
        new_attempt = retry_attempt + 1
        job = await db.get_job(pool, job_id)
        processing_key = (job or {}).get("minio_processing_key", "")
        dag_flow_id = (job or {}).get("dag_flow_id")
        await db.upsert_job(pool, job_id, retry_count=new_attempt, status="queued")
        log.warning("Job %s stage %s failed (attempt %d/%d) — retrying in %ds",
                    job_id, stage, new_attempt, MAX_RETRIES, RETRY_BACKOFF)
        asyncio.create_task(_retry_after(
            pool, redis, job_id, dag_flow_id, processing_key,
            new_attempt, stage,
        ))
    else:
        await db.upsert_job(pool, job_id, status="failed", error_msg=error_msg,
                            completed_at=time.time())
        log.error("Job %s permanently failed after %d attempts: %s",
                  job_id, retry_attempt, error_msg)


async def _retry_after(
    pool: asyncpg.Pool,
    redis: aioredis.Redis,
    job_id: str,
    flow_id: Optional[str],
    processing_key: str,
    retry_attempt: int,
    resume_stage: str,
) -> None:
    await asyncio.sleep(RETRY_BACKOFF)
    await trigger_job(pool, redis, job_id, flow_id, processing_key,
                      retry_attempt=retry_attempt, resume_from_stage=resume_stage)
