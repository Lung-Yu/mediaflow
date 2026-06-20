"""PATCH /jobs/{id}/correction — apply transcript edits and manage verification flow."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.services import correction as correction_svc
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs/{job_id}/correction", tags=["correction"])


class SegmentEdit(BaseModel):
    index: int
    text: str


class CorrectionBody(BaseModel):
    segments: List[SegmentEdit]


@router.patch("")
async def patch_correction(job_id: str, body: CorrectionBody, request: Request):
    pool  = request.app.state.pool
    minio = minio_mod.get_client()
    try:
        await correction_svc.apply_correction(
            pool, minio, job_id, [s.model_dump() for s in body.segments]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.post("/finalize")
async def finalize_correction(job_id: str, request: Request):
    pool = request.app.state.pool
    await correction_svc.finalize_verification(pool, job_id)
    return {"ok": True}
