"""GET /jobs/{id}/segment/{index}/audio — on-demand audio clip with MinIO clips/ caching."""
from __future__ import annotations

import asyncio
import io
import json
import subprocess
import tempfile
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request

from api import db
from api import minio_client as minio_mod

router = APIRouter(tags=["clip"])

CLIP_TTL_SEC = 600  # 10 minutes presigned URL expiry


async def _generate_clip_bytes(
    minio_client,
    job_id: str,
    stem: str,
    start: float,
    end: float,
) -> bytes:
    """Download clean.wav from MinIO, ffmpeg-clip the segment, return bytes. Runs in executor."""
    def _sync() -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"{stem}_clean.wav"
            minio_client._s3.download_file(
                minio_client.output_bucket,
                f"output/{job_id}/{stem}_clean.wav",
                str(src),
            )
            out = Path(tmp) / "clip.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src),
                 "-ss", str(start), "-to", str(end),
                 "-c", "copy", str(out)],
                check=True, capture_output=True,
            )
            return out.read_bytes()

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync)


@router.get("/jobs/{job_id}/segment/{index}/audio")
async def get_segment_audio(job_id: str, index: int, request: Request):
    minio = minio_mod.get_client()

    pool = request.app.state.pool
    job = await db.get_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    stem = Path(job["filename"]).stem
    segments_key = f"output/{job_id}/{stem}_segments.json"

    try:
        obj = minio._s3.get_object(Bucket=minio.output_bucket, Key=segments_key)
        segments = json.loads(obj["Body"].read())
    except ClientError:
        raise HTTPException(status_code=404, detail="Segments not found — job may not be complete")

    if index < 0 or index >= len(segments):
        raise HTTPException(
            status_code=404,
            detail=f"Segment index {index} out of range (0–{len(segments) - 1})",
        )

    seg   = segments[index]
    start = seg["start"]
    end   = seg["end"]

    clip_key     = f"clips/{job_id}/{index}.wav"
    clips_bucket = minio.clips_bucket

    # Check cache
    try:
        minio._s3.head_object(Bucket=clips_bucket, Key=clip_key)
        # Cache hit — skip ffmpeg
    except ClientError:
        # Cache miss — generate and upload clip
        clip_bytes = await _generate_clip_bytes(minio, job_id, stem, start, end)
        minio._s3.upload_fileobj(
            io.BytesIO(clip_bytes),
            clips_bucket,
            clip_key,
            ExtraArgs={"ContentType": "audio/wav"},
        )

    url = minio.presign_get_url(clips_bucket, clip_key, expires_in=CLIP_TTL_SEC)
    return {"url": url, "expires_in": CLIP_TTL_SEC, "start": start, "end": end}
