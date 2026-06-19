"""POST /internal/stage-callback — receives stage results from Progress Worker."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.services import dag

router = APIRouter(prefix="/internal", tags=["internal"])


class StageCallbackBody(BaseModel):
    job_id: str
    stage: str
    status: Literal["success", "failed"]
    retry_attempt: int = 0
    error_msg: Optional[str] = None
    output_keys: Optional[list[str]] = None


@router.post("/stage-callback")
async def stage_callback(body: StageCallbackBody, request: Request):
    pool = request.app.state.pool
    redis = request.app.state.redis
    await dag.handle_stage_callback(
        pool, redis,
        job_id=body.job_id,
        stage=body.stage,
        status=body.status,
        retry_attempt=body.retry_attempt,
        error_msg=body.error_msg,
    )
    return {"ok": True}
