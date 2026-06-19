"""GET /jobs and GET /jobs/{id} — job status endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api import db

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    pool = request.app.state.pool
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job


@router.get("")
async def list_jobs(request: Request):
    pool = request.app.state.pool
    overview = await db.get_status_overview(pool)
    return overview
