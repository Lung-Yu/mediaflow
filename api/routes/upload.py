"""Upload API routes — multipart presigned URL flow."""
import math
import os
import re
import time
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import db
from api.utils import minio as minio_mod

router = APIRouter(prefix="/upload")

MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(5 * 1024 ** 3)))
PART_SIZE = int(os.getenv("UPLOAD_PART_SIZE_BYTES", str(100 * 1024 * 1024)))


class InitRequest(BaseModel):
    filename: str
    size_bytes: int
    content_type: str = "application/octet-stream"


class PartInfo(BaseModel):
    part_number: int
    etag: str


class CompleteRequest(BaseModel):
    upload_id: str
    minio_key: str
    parts: List[PartInfo]
    initial_prompt: str = ""


def _stem_from_filename(filename: str) -> str:
    name = filename.rsplit(".", 1)[0]
    name = re.sub(r"[^\w\-]", "_", name.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "upload"


@router.post("/init")
async def upload_init(req: InitRequest):
    """Initiate a multipart upload. Returns presigned part URLs for direct browser upload."""
    if req.size_bytes > MAX_FILE_BYTES:
        raise HTTPException(400, f"File too large ({req.size_bytes} bytes). Max: {MAX_FILE_BYTES}")

    stem = _stem_from_filename(req.filename)
    existing = await db.get_task(stem)
    if existing and existing["status"] not in ("completed", "failed"):
        raise HTTPException(409, f"Task '{stem}' already active (status={existing['status']})")

    minio_key = f"{stem}/{req.filename}"
    client = minio_mod.get_client()
    upload_id = client.create_multipart_upload(minio_key)
    num_parts = math.ceil(req.size_bytes / PART_SIZE)
    parts = client.presign_part_urls(minio_key, upload_id, num_parts)

    return {
        "upload_id": upload_id,
        "minio_key": minio_key,
        "stem": stem,
        "part_size": PART_SIZE,
        "parts": parts,
    }


@router.post("/complete")
async def upload_complete(req: CompleteRequest, request: Request):
    """Finalise multipart upload and hand off to Project Service."""
    filename = req.minio_key.split("/", 1)[1]
    client = minio_mod.get_client()
    client.complete_multipart_upload(
        req.minio_key,
        req.upload_id,
        [{"part_number": p.part_number, "etag": p.etag} for p in req.parts],
    )
    from api.services.project import on_upload_trigger
    job_id = await on_upload_trigger(
        request.app.state.pool,
        request.app.state.redis,
        client,
        file_key=req.minio_key,
        filename=filename,
        dag_flow_id=None,
        submitted_by="anonymous",
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/queue")
async def get_queue():
    """List all upload-originated tasks with status and presigned download URLs."""
    tasks = await db.get_upload_queue()
    client = minio_mod.get_client()
    result = []
    for t in tasks:
        entry = {
            "stem": t["stem"],
            "filename": t["filename"],
            "status": t["status"],
            "queued_at": t.get("submitted_at"),
            "current_stage": t.get("current_stage"),
            "error_msg": t.get("error_msg"),
            "downloads": {},
        }
        if t["status"] == "completed" and t.get("minio_output_prefix"):
            prefix = t["minio_output_prefix"]
            output_bucket = minio_mod.OUTPUT_BUCKET
            for suffix in [".srt", "_summary.md", "_summary.json"]:
                key = f"{prefix}{t['stem']}{suffix}"
                try:
                    entry["downloads"][suffix] = client.presign_get_url(output_bucket, key)
                except Exception:
                    pass
        result.append(entry)
    return result


@router.delete("/queue/{stem}")
async def cancel_upload(stem: str):
    """Cancel a pending upload task. Returns 409 if task is already processing."""
    task = await db.get_task(stem)
    if not task:
        raise HTTPException(404, f"Task '{stem}' not found")
    if task["status"] != "pending":
        raise HTTPException(409, f"Cannot cancel task with status '{task['status']}'")
    await db.delete_task(stem)
    return {"cancelled": stem}
