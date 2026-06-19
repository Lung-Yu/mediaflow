"""Job intake — FR6 validation + MinIO verify + job creation + DAG trigger."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import PurePosixPath
from typing import Optional

import asyncpg
from botocore.exceptions import ClientError

from api import db
from api.services import dag

log = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {".mp4", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".webm"}
_MAX_FILENAME_LEN = 255


def validate_fr6(filename: str, size_bytes: int, max_bytes: int) -> tuple:
    """FR6 Phase 1: filename anomaly + size check. Returns (ok, reason). Pure function."""
    # Null bytes
    if "\x00" in filename:
        return False, "FR6: filename contains null character"

    # Path traversal
    parts = PurePosixPath(filename).parts
    if any(p in ("..", ".") or p.startswith("/") for p in parts):
        return False, "FR6: filename contains path traversal characters"
    if "\\" in filename or "/" in filename:
        return False, "FR6: filename must not contain directory separators"

    # Length
    if len(filename) > _MAX_FILENAME_LEN:
        return False, f"FR6: filename too long ({len(filename)} > {_MAX_FILENAME_LEN})"

    # Extension
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        return False, f"FR6: unsupported file extension {suffix!r}"

    # Size
    if size_bytes == 0:
        return False, "FR6: file is empty"
    if size_bytes > max_bytes:
        return False, f"FR6: file size {size_bytes} exceeds limit {max_bytes}"

    return True, ""


async def create_job(
    pool: asyncpg.Pool,
    redis,
    minio_client,
    input_key: str,
    dag_flow: Optional[str],
    max_file_bytes: int,
) -> str:
    """Verify MinIO → FR6 → copy to processing → create job row → trigger DAG.

    Returns job_id string.
    Raises FileNotFoundError if input_key not found in MinIO.
    Raises ValueError with FR6 reason if file fails security checks.
    """
    filename = input_key.split("/")[-1]

    # Verify file exists in MinIO input/ and get size
    try:
        head = minio_client._s3.head_object(
            Bucket=minio_client.input_bucket, Key=input_key
        )
    except ClientError as e:
        raise FileNotFoundError(
            f"File not found in MinIO input bucket: {input_key!r}"
        ) from e

    size_bytes = head["ContentLength"]

    # FR6 check — runs before any state mutation
    ok, reason = validate_fr6(filename, size_bytes, max_file_bytes)
    if not ok:
        raise ValueError(reason)

    job_id = str(uuid.uuid4())

    # Copy input → processing (7d TTL bucket — safe from 1h input TTL)
    processing_key = minio_client.copy_input_to_processing(input_key, job_id)

    # Create job row
    await db.upsert_job(
        pool, job_id,
        filename=filename,
        submitted_by="anonymous",
        status="submitted",
        submitted_at=time.time(),
        minio_input_key=input_key,
        minio_processing_key=processing_key,
    )

    # Trigger DAG orchestration
    await dag.trigger_job(pool, redis, job_id, dag_flow, processing_key)

    log.info("Job %s created (file=%s, flow=%s)", job_id, filename, dag_flow)
    return job_id
