"""Receive stage-complete events pushed by the pipeline watcher."""
import time
import json
from fastapi import APIRouter
from pydantic import BaseModel
from api import db

router = APIRouter(prefix="/events")

# Status transitions driven by event type + stage
_STATUS_MAP = {
    "task.submitted":  "submitted",
    "stage.completed": "processing",
    "task.completed":  "completed",
    "task.failed":     "failed",
}


class StageEvent(BaseModel):
    event: str        # task.submitted | stage.completed | task.completed | task.failed
    stem: str
    filename: str = ""
    stage: str = ""   # preprocessing | transcription | verification | summary
    status: str = ""  # done | failed
    output_path: str = ""
    error_msg: str = ""
    ts: float = 0.0


@router.post("/stage-complete", status_code=202)
async def receive_event(evt: StageEvent):
    ts = evt.ts or time.time()
    new_status = _STATUS_MAP.get(evt.event, "processing")

    task_fields: dict = {"status": new_status}

    if evt.filename:
        task_fields["filename"] = evt.filename
    if evt.event == "task.submitted":
        task_fields["submitted_at"] = ts
    if evt.event == "stage.completed" and evt.stage == "preprocessing":
        task_fields["started_at"] = ts
    if evt.stage:
        task_fields["current_stage"] = evt.stage
    if evt.event in ("task.completed", "task.failed"):
        task_fields["completed_at"] = ts
    if evt.output_path:
        task_fields["output_srt_path"] = evt.output_path
    if evt.error_msg:
        task_fields["error_msg"] = evt.error_msg

    await db.upsert_task(evt.stem, **task_fields)
    await db.insert_event(
        stem=evt.stem,
        event=evt.event,
        stage=evt.stage,
        status=evt.status,
        ts=ts,
        payload=json.dumps(evt.model_dump()),
    )

    return {"received": True, "stem": evt.stem, "new_status": new_status}
