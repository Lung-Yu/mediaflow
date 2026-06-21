"""Internal HTTP endpoint — Progress Worker calls this after each pipeline stage."""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.services.dag import handle_stage_callback

router = APIRouter(prefix="/internal")
log = logging.getLogger(__name__)


class StageCallbackRequest(BaseModel):
    job_id: str
    stage: str
    status: str          # "success" | "failed"
    retry_attempt: int = 0
    error_msg: Optional[str] = None


@router.post("/stage-callback", status_code=204)
async def stage_callback(req: StageCallbackRequest, request: Request):
    pool = request.app.state.pool
    redis = request.app.state.redis
    log.info("Stage callback: job=%s stage=%s status=%s", req.job_id, req.stage, req.status)
    await handle_stage_callback(
        pool, redis,
        req.job_id, req.stage, req.status, req.retry_attempt, req.error_msg,
    )
