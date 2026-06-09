"""Task timeline — per-stage timing for a completed task."""
from fastapi import APIRouter, HTTPException
from api import db

router = APIRouter(prefix="/tasks")


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
