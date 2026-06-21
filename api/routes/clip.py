"""Segment clip endpoint — on-demand ffmpeg clip from processing audio."""
from __future__ import annotations
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request

from api.db.queries import get_job
from api.utils import minio as minio_mod

router = APIRouter(prefix="/jobs/{job_id}/segment", tags=["clip"])


def _extract_clip(src: Path, start: float, end: float, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-to", str(end),
         "-i", str(src), "-c:a", "libmp3lame", "-q:a", "4", str(dest)],
        check=True, capture_output=True,
    )


def _build_clip(client, processing_key: str, job_id: str, index: int,
                start: float, end: float) -> str:
    """Download source audio, clip, upload, return presigned URL."""
    clip_key = f"clips/{job_id}/seg_{index}.mp3"
    with tempfile.TemporaryDirectory(prefix=f"clip_{job_id}_") as td:
        src = Path(td) / Path(processing_key).name
        client.download_file_from(processing_key, src, bucket=client.processing_bucket)
        dest = Path(td) / f"seg_{index}.mp3"
        _extract_clip(src, start, end, dest)
        client.upload_file(clip_key, dest, bucket=client.clips_bucket)
    return client.presign_get_url(client.clips_bucket, clip_key, expires_in=3600)


@router.get("/{index}/audio")
async def get_segment_audio(job_id: str, index: int, request: Request):
    pool = request.app.state.pool
    client = minio_mod.get_client()

    job = await get_job(pool, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    if not job.get("minio_processing_key"):
        raise HTTPException(409, "No processing audio available")

    clip_key = f"clips/{job_id}/seg_{index}.mp3"
    try:
        client.head_object(clip_key, bucket=client.clips_bucket)
        url = client.presign_get_url(client.clips_bucket, clip_key, expires_in=3600)
        return {"url": url}
    except ClientError:
        pass  # ponytail: cache miss → generate below

    seg_key = f"output/{job_id}/{job_id}_segments.json"
    try:
        raw = client.get_bytes(seg_key, bucket=client.output_bucket)
        segments = json.loads(raw)
    except ClientError:
        raise HTTPException(404, "segments.json not found for this job")

    matches = [s for s in segments if s.get("id") == index]
    if not matches:
        raise HTTPException(404, f"Segment {index} not found")
    seg = matches[0]
    start, end = float(seg["start"]), float(seg["end"])

    loop = asyncio.get_running_loop()
    url = await loop.run_in_executor(
        None, _build_clip, client, job["minio_processing_key"], job_id, index, start, end
    )
    return {"url": url}
