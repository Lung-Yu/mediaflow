"""Project Service — job intake, FR6 security check, DAG trigger."""
from __future__ import annotations
import os
import time
import uuid

import asyncpg
from fastapi import HTTPException

from api.db.queries import upsert_job
from api.services.dag import trigger_job, check_capacity, CapacityError

_MAX_FILE_BYTES = int(os.getenv("UPLOAD_MAX_FILE_BYTES", str(5 * 1024 ** 3)))
_MAX_FILENAME_LEN = 255


def validate_fr6(filename: str, file_size_bytes: int) -> str | None:
    """FR6 Phase 1: filename anomaly + size check. Returns error string or None."""
    if "\x00" in filename:
        return "Filename contains null byte"
    if ".." in filename or filename.startswith("/"):
        return "Filename path traversal detected"
    if len(filename) > _MAX_FILENAME_LEN:
        return f"Filename too long (max {_MAX_FILENAME_LEN})"
    if file_size_bytes <= 0:
        return "File is empty"
    if file_size_bytes > _MAX_FILE_BYTES:
        return f"File too large ({file_size_bytes} bytes, max {_MAX_FILE_BYTES})"
    return None


async def on_upload_trigger(
    pool: asyncpg.Pool,
    redis,
    minio,
    file_key: str,
    filename: str,
    dag_flow_id: str | None,
    submitted_by: str = "anonymous",
) -> str:
    """Verify upload, FR6 check, copy to processing/, create job, trigger DAG."""
    # 0. capacity check — before any side effects
    try:
        await check_capacity(pool)
    except CapacityError as exc:
        raise HTTPException(429, str(exc))

    # 1. verify file in MinIO input/
    try:
        meta = minio.head_object(file_key)
        file_size = meta["ContentLength"]
    except Exception as exc:
        raise HTTPException(400, f"File not found in storage: {exc}")

    # 2. FR6 check
    err = validate_fr6(filename, file_size)
    if err:
        raise HTTPException(400, f"FR6 validation failed: {err}")

    # 3. copy input/ → processing/ using existing client method
    job_id = _make_job_id(filename)
    processing_key = minio.copy_input_to_processing(file_key, job_id)

    # 4. create job row (minimal; DAG-Service fills in dag_flow_id, status=queued)
    await upsert_job(
        pool, job_id,
        filename=filename,
        submitted_by=submitted_by,
        status="submitted",
        minio_input_key=file_key,
        minio_processing_key=processing_key,
        submitted_at=time.time(),
    )

    # 5. trigger DAG-Service
    await trigger_job(pool, redis, job_id, filename, processing_key, dag_flow_id)

    return job_id


def _make_job_id(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0][:20].replace(" ", "_")
    return f"{uuid.uuid4().hex[:8]}_{stem}"
