"""Receive stage-complete events pushed directly by the pipeline watcher via HTTP."""
import time
from fastapi import APIRouter
from pydantic import BaseModel
from api.services.event_processor import process_event

router = APIRouter(prefix="/events")


class StageEvent(BaseModel):
    event: str
    stem: str
    filename: str = ""
    stage: str = ""
    status: str = ""
    output_path: str = ""
    error_msg: str = ""
    ts: float = 0.0


@router.post("/stage-complete", status_code=202)
async def receive_event(evt: StageEvent):
    fields = evt.model_dump()
    fields.setdefault("ts", time.time())
    new_status = await process_event(fields)
    return {"received": True, "stem": evt.stem, "new_status": new_status}
