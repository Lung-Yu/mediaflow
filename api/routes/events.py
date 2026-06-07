"""Receive stage-complete events pushed by the pipeline watcher."""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/events")


class StageEvent(BaseModel):
    event: str       # task.submitted | stage.completed | task.completed | task.failed
    stem: str        # file stem (e.g. "gtalent-advanced-20260521-lesson21")
    stage: str = ""  # preprocessing | transcription | verification | summary
    status: str = "" # done | failed
    output_path: str = ""
    error_msg: str = ""


@router.post("/stage-complete", status_code=202)
def receive_event(evt: StageEvent):
    # TODO: persist to pipeline.db
    return {"received": True, "stem": evt.stem, "event": evt.event}
