"""Shared event-processing logic — used by both the HTTP route and Redis consumer."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from api import db
from api.services.webhook import notify

log = logging.getLogger(__name__)

_STAGE_STATUS = {
    "preprocess":      "preprocessing",
    "transcribe":      "transcribing",
    "verify_segments": "verifying",
    "correct_srt":     "correcting",
    "diarize":         "diarizing",
    "summarize":       "summarizing",
    "detect_chapters": "detecting_chapters",
}

_EVENT_STATUS = {
    "task.submitted": "submitted",
    "task.completed": "completed",
    "task.failed":    "failed",
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

    if event == "stage.started":
        new_status = _STAGE_STATUS.get(fields.get("stage", ""), "processing")
    elif event == "stage.completed":
        new_status = None  # keep current status until next stage.started
    else:
        new_status = _EVENT_STATUS.get(event, "processing")

    task_fields: dict = {}
    if new_status is not None:
        task_fields["status"] = new_status

    if fields.get("filename"):
        task_fields["filename"] = fields["filename"]
    if event == "task.submitted":
        task_fields["submitted_at"] = ts
    if event == "stage.started" and fields.get("stage") == "preprocess":
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

    if event == "task.completed":
        try:
            from api.utils import minio as minio_mod
            client = minio_mod.get_client()
            output_dir = Path(os.getenv("WORKSPACE_DIR", "./workspace")) / "3_output"
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, client.upload_outputs, stem, output_dir)
            await db.upsert_task(stem, minio_output_prefix=f"{stem}/")
        except Exception as exc:
            log.warning("MinIO output backup failed for %s: %s", stem, exc)

    return new_status if new_status is not None else "processing"
