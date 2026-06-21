"""GET /jobs, POST /jobs, GET /jobs/{id} — job endpoints."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import db
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    file_key: str
    filename: str
    dag_flow: Optional[str] = None
    submitted_by: str = "anonymous"


@router.post("", status_code=201)
async def create_job(req: CreateJobRequest, request: Request):
    """Create a job from an already-uploaded MinIO file."""
    from api.services.project import on_upload_trigger
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


@router.get("")
async def list_jobs():
    return await db.get_status_overview()
