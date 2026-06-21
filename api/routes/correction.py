"""FR4 — Transcript correction endpoints."""
from __future__ import annotations
from typing import List

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.services.correction import apply_correction, finalize_correction
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs/{job_id}/correction", tags=["correction"])


class Segment(BaseModel):
    index: int
    start: float
    end: float
    text: str


class CorrectionRequest(BaseModel):
    segments: List[Segment]


@router.patch("", status_code=204)
async def patch_correction(job_id: str, req: CorrectionRequest, request: Request):
    await apply_correction(
        request.app.state.pool,
        minio_mod.get_client(),
        job_id,
        [s.model_dump() for s in req.segments],
    )


@router.post("/finalize", status_code=204)
async def post_finalize(job_id: str, request: Request):
    await finalize_correction(request.app.state.pool, job_id)
