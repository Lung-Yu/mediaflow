"""GET /jobs, GET /jobs/{id}, and POST /jobs — job status and creation endpoints."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import db
from api import minio_client as minio_mod
from api.services import project as project_svc

router = APIRouter(prefix="/jobs", tags=["jobs"])

UPLOAD_MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(500 * 1024 * 1024)))


class CreateJobBody(BaseModel):
    file_key: str
    dag_flow: Optional[str] = None


@router.post("", status_code=201)
async def create_job(body: CreateJobBody, request: Request):
    pool = request.app.state.pool
    redis = request.app.state.redis
    minio = minio_mod.get_client()
    try:
        job_id = await project_svc.create_job(
            pool=pool, redis=redis, minio_client=minio,
            input_key=body.file_key, dag_flow=body.dag_flow,
            max_file_bytes=UPLOAD_MAX_FILE_BYTES,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    pool = request.app.state.pool
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job


@router.get("")
async def list_jobs(request: Request):
    overview = await db.get_status_overview()
    return overview
