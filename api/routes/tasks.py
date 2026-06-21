"""Task management — rerun, delete, and timeline."""
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import db

router = APIRouter(prefix="/tasks")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
_VALID_STAGES = frozenset({
    "preprocess", "transcribe", "verify_segments", "correct_srt",
    "diarize", "summarize", "detect_chapters",
})
_ACTIVE_STATUSES = {
    "pending", "downloading", "queued", "submitted", "processing",
}


class RunRequest(BaseModel):
    from_stage: Optional[str] = None


@router.post("/{stem}/runs", status_code=201)
async def create_run(stem: str, req: RunRequest):
    """Queue a new pipeline run for an existing task. from_stage=null means full restart."""
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task["status"] in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Task {stem!r} is already active (status={task['status']}). Wait for it to complete before rerunning.",
        )

    if req.from_stage and req.from_stage not in _VALID_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown stage: {req.from_stage!r}. Valid: {sorted(_VALID_STAGES)}",
        )

    await db.insert_rerun(stem, req.from_stage)
    await db.upsert_task(stem, status="submitted", error_msg=None)
    return {"stem": stem, "from_stage": req.from_stage, "status": "submitted"}


@router.delete("/{stem}")
async def delete_task_route(stem: str):
    """Delete a task record and remove any queued input file."""
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("filename"):
        for candidate in [
            WORKSPACE / "1_input" / task["filename"],
            WORKSPACE / "1_input" / (task["filename"] + ".failed"),
        ]:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass

    await db.delete_task(stem)
    return {"deleted": stem}


@router.get("/{stem}/timeline")
async def get_timeline(stem: str):
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    stage_events = await db.get_stage_events(stem)

    submitted = task.get("submitted_at")
    prev_ts = submitted
    stage_list = []
    for ev in stage_events:
        ts = ev["ts"]
        duration = round(ts - prev_ts) if prev_ts is not None else None
        stage_list.append({
            "stage": ev["stage"],
            "completed_at": ts,
            "duration_sec": duration,
        })
        prev_ts = ts

    total_pipeline = sum(
        s["duration_sec"] for s in stage_list if s["duration_sec"] is not None
    )
    completed = task.get("completed_at")
    total_wall = round(completed - submitted) if completed and submitted else None

    return {
        "stem": stem,
        "filename": task.get("filename"),
        "submitted_at": submitted,
        "started_at": task.get("started_at"),
        "completed_at": completed,
        "total_pipeline_sec": total_pipeline,
        "total_wall_sec": total_wall,
        "stages": stage_list,
    }
