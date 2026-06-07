"""Shared event-processing logic — used by both the HTTP route and Redis consumer."""
import json
import time
from api import db
from api.webhook import notify

_STATUS_MAP = {
    "task.submitted":  "submitted",
    "stage.completed": "processing",
    "task.completed":  "completed",
    "task.failed":     "failed",
}
_TERMINAL = {"task.completed", "task.failed"}


async def process_event(fields: dict) -> str:
    """Upsert task state, record event row, fire webhook if terminal.

    `fields` is a flat dict matching the StageEvent schema (all values are str or numeric).
    Returns the new status string.
    """
    event = fields.get("event", "")
    stem = str(fields.get("stem", ""))
    ts = float(fields.get("ts") or time.time())

    new_status = _STATUS_MAP.get(event, "processing")
    task_fields: dict = {"status": new_status}

    if fields.get("filename"):
        task_fields["filename"] = fields["filename"]
    if event == "task.submitted":
        task_fields["submitted_at"] = ts
    if event == "stage.completed" and fields.get("stage") == "preprocessing":
        task_fields["started_at"] = ts
    if fields.get("stage"):
        task_fields["current_stage"] = fields["stage"]
    if event in _TERMINAL:
        task_fields["completed_at"] = ts
    if fields.get("output_path"):
        task_fields["output_srt_path"] = fields["output_path"]
    if fields.get("error_msg"):
        task_fields["error_msg"] = fields["error_msg"]

    await db.upsert_task(stem, **task_fields)
    await db.insert_event(
        stem=stem,
        event=event,
        stage=str(fields.get("stage", "")),
        status=str(fields.get("status", "")),
        ts=ts,
        payload=json.dumps(fields),
    )

    if event in _TERMINAL:
        await notify({
            "stem": stem,
            "status": new_status,
            "event": event,
            "output_srt_path": fields.get("output_path", ""),
            "error_msg": fields.get("error_msg", ""),
            "completed_at": ts,
        })

    return new_status
