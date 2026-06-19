"""asyncpg-backed query functions. Each function takes pool: asyncpg.Pool as first arg."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import asyncpg


async def init(pool: asyncpg.Pool) -> None:
    """Run migrations if tables don't exist. Called from FastAPI lifespan."""
    import pathlib
    migrations_dir = pathlib.Path(__file__).parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        sql = sql_file.read_text()
        await pool.execute(sql)


_ALLOWED_JOB_COLUMNS = frozenset({
    "filename", "submitted_by", "dag_flow_id", "status", "current_stage",
    "submitted_at", "started_at", "completed_at", "retry_count", "error_msg",
    "output_srt_path", "corrected_srt_path", "verification_status",
    "verified_at", "verified_by", "minio_input_key", "minio_processing_key",
})


async def upsert_job(pool: asyncpg.Pool, job_id: str, **kwargs: Any) -> None:
    bad = set(kwargs) - _ALLOWED_JOB_COLUMNS
    if bad:
        raise ValueError(f"upsert_job: unknown columns: {bad}")
    cols = list(kwargs.keys())
    vals = list(kwargs.values())
    col_list = ", ".join(cols)
    placeholder_list = ", ".join(f"${i+2}" for i in range(len(cols)))
    update_list = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    sql = f"""
        INSERT INTO jobs (id, {col_list})
        VALUES ($1, {placeholder_list})
        ON CONFLICT (id) DO UPDATE SET {update_list}
    """
    await pool.execute(sql, job_id, *vals)


async def get_job(pool: asyncpg.Pool, job_id: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    return dict(row) if row else None


async def insert_event(
    pool: asyncpg.Pool, job_id: str, stage: str, status: Optional[str],
    retry_attempt: int = 0, error_msg: Optional[str] = None,
    payload: Optional[str] = None, ts: Optional[float] = None,
) -> None:
    await pool.execute(
        """INSERT INTO events (job_id, stage, status, retry_attempt, error_msg, payload, ts)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        job_id, stage, status, retry_attempt, error_msg, payload, ts or time.time(),
    )


async def get_status_overview(pool: asyncpg.Pool) -> dict:
    active_statuses = ("queued", "processing")
    rows_processing = await pool.fetch(
        "SELECT * FROM jobs WHERE status = ANY($1::text[]) ORDER BY started_at DESC",
        list(active_statuses),
    )
    rows_queue = await pool.fetch(
        "SELECT * FROM jobs WHERE status = 'submitted' ORDER BY submitted_at ASC"
    )
    rows_recent = await pool.fetch(
        "SELECT * FROM jobs WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 20"
    )
    rows_failed = await pool.fetch(
        "SELECT * FROM jobs WHERE status = 'failed' ORDER BY completed_at DESC LIMIT 10"
    )
    return {
        "processing": [dict(r) for r in rows_processing],
        "queue":      [dict(r) for r in rows_queue],
        "recent":     [dict(r) for r in rows_recent],
        "failed":     [dict(r) for r in rows_failed],
    }


async def count_active_jobs(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('submitted','queued','processing')"
    )
    return row["count"] if row else 0


async def get_dag_flow(pool: asyncpg.Pool, flow_id: Optional[str]) -> dict:
    if flow_id:
        row = await pool.fetchrow(
            "SELECT * FROM dag_flows WHERE id = $1 AND deprecated = false", flow_id
        )
    else:
        row = await pool.fetchrow(
            "SELECT * FROM dag_flows WHERE is_default = true AND deprecated = false LIMIT 1"
        )
    if not row:
        raise ValueError(f"dag_flow not found: {flow_id!r}")
    result = dict(row)
    if isinstance(result.get("stage_plan"), str):
        result["stage_plan"] = json.loads(result["stage_plan"])
    return result


async def get_task_aggregates(pool: asyncpg.Pool) -> dict:
    row = await pool.fetchrow(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(completed_at - started_at), 0) AS total_duration,
                  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
           FROM jobs"""
    )
    return {
        "total_tasks":        row["total"] or 0,
        "total_duration_sec": float(row["total_duration"] or 0),
        "completed":          row["completed"] or 0,
    }


async def get_stage_events(pool: asyncpg.Pool, job_id: str) -> list:
    rows = await pool.fetch(
        """SELECT stage, ts FROM events
           WHERE job_id = $1 AND status = 'success' AND stage IS NOT NULL
           ORDER BY ts ASC""",
        job_id,
    )
    return [dict(r) for r in rows]
