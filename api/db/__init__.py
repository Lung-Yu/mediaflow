"""Pool-aware wrappers for routes that use `from api import db`."""
from __future__ import annotations

from api.db.queries import (
    init,
    upsert_job,
    get_job,
    insert_event,
    count_active_jobs,
    get_dag_flow,
    get_status_overview as _get_status_overview_q,
    get_task_aggregates as _get_task_aggregates_q,
)

__all__ = [
    "init", "upsert_job", "get_job", "insert_event",
    "count_active_jobs", "get_dag_flow",
    "get_status_overview", "get_task_aggregates",
]


def _get_pool():
    from api.main import app
    return app.state.pool


async def get_status_overview() -> dict:
    result = await _get_status_overview_q(_get_pool())
    for key in ("processing", "queue", "recent", "failed"):
        for d in result.get(key, []):
            d["stem"] = d["id"]
    return result


async def get_task_aggregates() -> dict:
    return await _get_task_aggregates_q(_get_pool())
