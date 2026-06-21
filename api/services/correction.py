"""FR4 Transcript Correction — rebuild corrected SRT, write to MinIO output/."""
from __future__ import annotations
import time
from typing import Optional

import asyncpg
from fastapi import HTTPException

from api.db.queries import get_job, upsert_job


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def rebuild_srt(segments: list[dict]) -> str:
    """Build SRT content from [{index, start, end, text}] list."""
    blocks = []
    for seg in segments:
        blocks.append(
            f"{seg['index']}\n"
            f"{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}\n"
            f"{seg['text'].strip()}"
        )
    return "\n\n".join(blocks) + "\n"


async def apply_correction(
    pool: asyncpg.Pool,
    minio,
    job_id: str,
    segments: list[dict],
) -> None:
    job = await get_job(pool, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    if job["status"] not in ("completed",):
        raise HTTPException(409, "Correction only allowed on completed jobs")

    srt_content = rebuild_srt(segments)
    corrected_key = f"output/{job_id}/{job_id}_corrected.srt"
    minio.put_bytes(corrected_key, srt_content.encode(), bucket=minio.output_bucket)

    new_vstatus = (
        "in_progress"
        if job.get("verification_status") == "unverified"
        else job["verification_status"]
    )
    await upsert_job(
        pool, job_id,
        corrected_srt_path=corrected_key,
        verification_status=new_vstatus,
    )


async def finalize_correction(pool: asyncpg.Pool, job_id: str) -> None:
    job = await get_job(pool, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    await upsert_job(
        pool, job_id,
        verification_status="verified",
        verified_at=time.time(),
        verified_by="user",
    )


def _demo():
    srt = rebuild_srt([
        {"index": 1, "start": 0.0, "end": 2.5, "text": "Hello"},
        {"index": 2, "start": 3.0, "end": 5.0, "text": "World"},
    ])
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "00:00:03,000 --> 00:00:05,000" in srt
    print("correction self-check: OK")


if __name__ == "__main__":
    _demo()
