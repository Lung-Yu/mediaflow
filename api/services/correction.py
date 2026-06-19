"""FR4 transcript correction: patch segment text and finalize verification status."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

import asyncpg

from api import db


def _seconds_to_srt_ts(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(segments: List[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _seconds_to_srt_ts(seg["start"])
        end   = _seconds_to_srt_ts(seg["end"])
        text  = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


async def apply_correction(
    pool: asyncpg.Pool,
    minio_client,
    job_id: str,
    edits: List[dict],   # [{index: int, text: str}]
) -> str:
    job = await db.get_job(pool, job_id)
    if not job:
        raise ValueError(f"Job {job_id!r} not found")

    stem = Path(job["filename"]).stem

    # Load segments from MinIO output/
    obj = minio_client._s3.get_object(
        Bucket=minio_client.output_bucket,
        Key=f"output/{job_id}/{stem}_segments.json",
    )
    segments: List[dict] = json.loads(obj["Body"].read())

    # Apply edits (full text replacement per list position)
    edit_map = {e["index"]: e["text"] for e in edits}
    new_segments = []
    for pos, seg in enumerate(segments):
        new_seg = dict(seg)
        if pos in edit_map:
            new_seg["text"] = edit_map[pos]
        new_segments.append(new_seg)

    corrected_srt = _build_srt(new_segments)

    # Write corrected SRT to MinIO output/
    corrected_key = f"output/{job_id}/{stem}_corrected.srt"
    minio_client._s3.put_object(
        Bucket=minio_client.output_bucket,
        Key=corrected_key,
        Body=corrected_srt.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )

    # Update job row
    updates: dict = {"corrected_srt_path": corrected_key}
    if job["verification_status"] == "unverified":
        updates["verification_status"] = "in_progress"
    await db.upsert_job(pool, job_id, **updates)

    return corrected_srt


async def finalize_verification(pool: asyncpg.Pool, job_id: str) -> None:
    await db.upsert_job(pool, job_id,
                        verification_status="verified",
                        verified_at=time.time())
