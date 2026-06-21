"""Public re-export shim.

Callers use `from api import db; await db.upsert_task(...)` — unchanged.
The shim pulls app.state.pool from the running FastAPI app so callers
don't need to pass the pool explicitly.
"""
from __future__ import annotations

from api.db.queries import (
    init,
    upsert_job,
    get_job,
    insert_event as _insert_event_q,
    get_status_overview as _get_status_overview_q,
    count_active_jobs,
    get_dag_flow,
    get_task_aggregates as _get_task_aggregates_q,
    get_stage_events as _get_stage_events_q,
)

__all__ = [
    "init", "upsert_job", "get_job", "insert_event", "get_status_overview",
    "count_active_jobs", "get_dag_flow", "get_task_aggregates", "get_stage_events",
    "upsert_task", "get_task", "count_active_tasks",
    "delete_task", "get_upload_queue", "get_oldest_pending",
]


def _get_pool():
    """Pull pool from running FastAPI app state."""
    from api.main import app
    return app.state.pool


# ── Pool-aware shims for functions the plan re-exports with no-arg signature ─

async def get_status_overview() -> dict:
    result = await _get_status_overview_q(_get_pool())
    for key in ("processing", "queue", "recent", "failed"):
        for d in result.get(key, []):
            d["stem"] = d["id"]
    return result


async def get_task_aggregates() -> dict:
    return await _get_task_aggregates_q(_get_pool())


async def get_stage_events(stem: str) -> list:
    return await _get_stage_events_q(_get_pool(), stem)


async def insert_event(stem: str, event: str = "", **kwargs) -> None:
    """Legacy shim: translate old (stem, event, **kwargs) API to new queries.insert_event."""
    pool = _get_pool()
    stage = str(kwargs.get("stage") or event or "")
    raw_status = str(kwargs.get("status") or "")
    status = raw_status if raw_status in ("started", "success", "failed") else None
    await _insert_event_q(
        pool,
        job_id=stem,
        stage=stage or "_",
        status=status,
        error_msg=kwargs.get("error_msg"),
        payload=kwargs.get("payload"),
        ts=kwargs.get("ts"),
    )


# ── Legacy shims — same names as old api/db.py ──────────────────────────────

async def upsert_task(stem: str, **kwargs) -> None:
    """Legacy shim: stem maps to job_id; filename is only written on INSERT, never overwritten."""
    kw = dict(kwargs)
    # Remove columns not in the new jobs table
    kw.pop("duration_sec", None)
    kw.pop("minio_output_prefix", None)
    # Map legacy status values to ones accepted by the CHECK constraint
    if "status" in kw:
        status_map = {
            "pending":            "submitted",
            "downloading":        "queued",
            "queued":             "queued",
            "preprocessing":      "processing",
            "transcribing":       "processing",
            "verifying":          "processing",
            "correcting":         "processing",
            "diarizing":          "processing",
            "summarizing":        "processing",
            "detecting_chapters": "processing",
        }
        kw["status"] = status_map.get(kw["status"], kw["status"])

    pool = _get_pool()
    # filename is used only for INSERT; never overwrite it on conflict
    insert_filename = kw.pop("filename", stem)

    if kw:
        cols = list(kw.keys())
        vals = list(kw.values())
        col_list = ", ".join(["filename"] + cols)
        ph_list = ", ".join(["$2"] + [f"${i+3}" for i in range(len(cols))])
        update_list = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        sql = (
            f"INSERT INTO jobs (id, {col_list}) "
            f"VALUES ($1, {ph_list}) "
            f"ON CONFLICT (id) DO UPDATE SET {update_list}"
        )
        await pool.execute(sql, stem, insert_filename, *vals)
    else:
        await pool.execute(
            "INSERT INTO jobs (id, filename) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            stem, insert_filename,
        )


async def get_task(stem: str) -> dict | None:
    result = await get_job(_get_pool(), stem)
    if result is not None:
        result["stem"] = result["id"]
    return result


async def count_active_tasks() -> int:
    return await count_active_jobs(_get_pool())


async def delete_task(stem: str) -> None:
    pool = _get_pool()
    await pool.execute("DELETE FROM events WHERE job_id = $1", stem)
    await pool.execute("DELETE FROM jobs WHERE id = $1", stem)


async def get_upload_queue() -> list:
    """All upload-originated tasks (minio_input_key IS NOT NULL), newest first."""
    rows = await _get_pool().fetch(
        "SELECT * FROM jobs WHERE minio_input_key IS NOT NULL ORDER BY submitted_at DESC"
    )
    results = []
    for r in rows:
        d = dict(r)
        d.setdefault("stem", d.get("id"))  # callers expect 'stem' key
        results.append(d)
    return results


async def get_oldest_pending() -> dict | None:
    """Return the oldest submitted task with minio_input_key, or None."""
    row = await _get_pool().fetchrow(
        "SELECT * FROM jobs WHERE status = 'submitted' AND minio_input_key IS NOT NULL "
        "ORDER BY submitted_at ASC LIMIT 1"
    )
    if row:
        d = dict(row)
        d.setdefault("stem", d.get("id"))
        return d
    return None


