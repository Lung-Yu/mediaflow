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
    insert_event,
    get_status_overview as _get_status_overview_q,
    count_active_jobs,
    get_dag_flow,
    get_task_aggregates,
    get_stage_events,
)

__all__ = [
    "init", "upsert_job", "get_job", "insert_event", "get_status_overview",
    "count_active_jobs", "get_dag_flow", "get_task_aggregates", "get_stage_events",
    # legacy shims below
    "upsert_task", "get_task", "count_active_tasks",
]


def _get_pool():
    """Pull pool from running FastAPI app state."""
    from api.main import app
    return app.state.pool


# ── New pool-aware shim for get_status_overview ──────────────────────────────

async def get_status_overview() -> dict:
    return await _get_status_overview_q(_get_pool())


# ── Legacy shims — same names as old api/db.py ──────────────────────────────

async def upsert_task(stem: str, **kwargs) -> None:
    """Legacy shim: stem maps to job_id; filename defaults to stem."""
    kw = dict(kwargs)
    if "filename" not in kw:
        kw["filename"] = stem
    await upsert_job(_get_pool(), stem, **kw)


async def get_task(stem: str) -> dict | None:
    return await get_job(_get_pool(), stem)


async def count_active_tasks() -> int:
    return await count_active_jobs(_get_pool())
