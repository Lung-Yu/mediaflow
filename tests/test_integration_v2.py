"""Integration smoke test — v2 architecture end-to-end:
on_upload_trigger → trigger_job (DAG) → stage callbacks → job completed
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

_FLOW = {
    "id": "general-v1",
    "stage_plan": [{"stage": "transcribe"}, {"stage": "summarize"}],
}


def _make_db_state():
    """Stateful in-memory DB stand-in shared across mocked query calls."""
    jobs = {}
    events = []

    async def upsert_job(pool, job_id, **kwargs):
        jobs.setdefault(job_id, {"id": job_id})
        jobs[job_id].update(kwargs)

    async def get_job(pool, job_id):
        return dict(jobs[job_id]) if job_id in jobs else None

    async def get_dag_flow(pool, flow_id):
        return _FLOW

    async def insert_event(pool, job_id, stage, status, **kwargs):
        events.append({"job_id": job_id, "stage": stage, "status": status})

    return jobs, events, upsert_job, get_job, get_dag_flow, insert_event


@pytest.mark.asyncio
async def test_full_v2_flow_completes():
    """Upload → trigger → two stage callbacks → job status == completed."""
    from api.services.project import on_upload_trigger
    from api.services.dag import handle_stage_callback

    jobs, events, upsert_job, get_job, get_dag_flow, insert_event = _make_db_state()

    minio = MagicMock()
    minio.head_object.return_value = {"ContentLength": 512 * 1024}
    minio.copy_input_to_processing.return_value = "processing/abc/test.m4a"
    redis = AsyncMock()

    with patch("api.services.project.upsert_job", upsert_job), \
         patch("api.services.project.check_capacity", AsyncMock()), \
         patch("api.services.dag.upsert_job", upsert_job), \
         patch("api.services.dag.get_job", get_job), \
         patch("api.services.dag.get_dag_flow", get_dag_flow), \
         patch("api.services.dag.insert_event", insert_event), \
         patch("api.services.dag._xadd", AsyncMock()):

        pool = AsyncMock()
        job_id = await on_upload_trigger(
            pool, redis, minio,
            file_key="input/test.m4a",
            filename="test.m4a",
            dag_flow_id=None,
        )
        assert job_id is not None
        assert jobs[job_id]["status"] == "queued"

        # Worker reports transcribe success
        await handle_stage_callback(pool, redis, job_id, "transcribe", "success", 0, None)
        assert jobs[job_id]["status"] == "processing"

        # Worker reports summarize success (last stage)
        await handle_stage_callback(pool, redis, job_id, "summarize", "success", 0, None)
        assert jobs[job_id]["status"] == "completed"

    assert len([e for e in events if e["status"] == "success"]) == 2


@pytest.mark.asyncio
async def test_stage_failure_retries_then_fails():
    """Stage failure retries up to _MAX_RETRIES times, then marks job failed."""
    import os
    os.environ["PIPELINE_MAX_RETRIES"] = "2"
    os.environ["PIPELINE_RETRY_BACKOFF_SEC"] = "0"

    import importlib
    import api.services.dag as dag_mod
    importlib.reload(dag_mod)

    jobs, events, upsert_job, get_job, get_dag_flow, insert_event = _make_db_state()
    jobs["job1"] = {
        "id": "job1", "dag_flow_id": "general-v1",
        "minio_processing_key": "processing/job1/test.m4a",
    }
    redis = AsyncMock()

    with patch("api.services.dag.upsert_job", upsert_job), \
         patch("api.services.dag.get_job", get_job), \
         patch("api.services.dag.get_dag_flow", get_dag_flow), \
         patch("api.services.dag.insert_event", insert_event), \
         patch("api.services.dag._enqueue_after_backoff"):

        pool = AsyncMock()
        # attempt 0 fails → retry (attempt < 2)
        await dag_mod.handle_stage_callback(
            pool, redis, "job1", "transcribe", "failed", 0, "OOM"
        )
        assert jobs["job1"]["status"] == "queued"

        # attempt 1 fails → retry (attempt < 2)
        await dag_mod.handle_stage_callback(
            pool, redis, "job1", "transcribe", "failed", 1, "OOM"
        )
        assert jobs["job1"]["status"] == "queued"

        # attempt 2 fails → no more retries
        await dag_mod.handle_stage_callback(
            pool, redis, "job1", "transcribe", "failed", 2, "OOM"
        )
        assert jobs["job1"]["status"] == "failed"
        assert jobs["job1"]["error_msg"] == "OOM"
