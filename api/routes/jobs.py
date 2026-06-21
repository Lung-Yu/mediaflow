"""GET /jobs, POST /jobs, GET /jobs/{id} — job endpoints."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import db
from api.db.queries import get_stage_events
from api.services.dag import trigger_job, check_capacity, CapacityError
from api.services.project import on_upload_trigger
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    file_key: str
    filename: str
    dag_flow: Optional[str] = None
    submitted_by: str = "anonymous"


@router.post("", status_code=201)
async def create_job(req: CreateJobRequest, request: Request):
    job_id = await on_upload_trigger(
        request.app.state.pool,
        request.app.state.redis,
        minio_mod.get_client(),
        file_key=req.file_key,
        filename=req.filename,
        dag_flow_id=req.dag_flow,
        submitted_by=req.submitted_by,
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    job = await db.get_job(request.app.state.pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job


@router.delete("/{job_id}", status_code=204)
async def cancel_job(job_id: str, request: Request):
    pool = request.app.state.pool
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    await pool.execute("DELETE FROM events WHERE job_id = $1", job_id)
    await pool.execute("DELETE FROM jobs WHERE id = $1", job_id)


@router.post("/{job_id}/rerun", status_code=201)
async def rerun_job(job_id: str, request: Request):
    pool = request.app.state.pool
    redis = request.app.state.redis
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    if not job.get("minio_processing_key"):
        raise HTTPException(status_code=409, detail="No processing audio to rerun from")
    try:
        await check_capacity(pool)
    except CapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    await trigger_job(
        pool, redis,
        job_id=job_id,
        filename=job["filename"],
        minio_processing_key=job["minio_processing_key"],
        dag_flow_id=job.get("dag_flow_id"),
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}/events")
async def get_job_events(job_id: str, request: Request):
    job = await db.get_job(request.app.state.pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return await get_stage_events(request.app.state.pool, job_id)


@router.get("")
async def list_jobs():
    return await db.get_status_overview()
