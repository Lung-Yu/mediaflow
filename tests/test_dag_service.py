import json
import pytest
from unittest.mock import AsyncMock, patch


def _make_pool(job=None):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=job)
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_redis():
    r = AsyncMock()
    r.xadd = AsyncMock(return_value="1-0")
    return r


FLOW = {
    "id": "general-v1",
    "stage_plan": [
        {"stage": "preprocess", "config": {}},
        {"stage": "transcribe", "config": {}},
        {"stage": "summarize", "config": {}},
    ],
    "is_default": True,
}

JOB = {
    "id": "job123",
    "filename": "test.m4a",
    "minio_processing_key": "processing/job123",
    "dag_flow_id": "general-v1",
    "status": "queued",
    "retry_count": 0,
}


@pytest.mark.asyncio
async def test_trigger_job_enqueues_mq():
    from api.services.dag import trigger_job
    pool = _make_pool()
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)):
        await trigger_job(pool, redis, "job123", "test.m4a", "processing/job123", None)
    redis.xadd.assert_called_once()
    fields = redis.xadd.call_args[0][1]
    assert fields["job_id"] == "job123"
    assert "stage_plan" in fields
    assert fields["resume_from_stage"] == "preprocess"


@pytest.mark.asyncio
async def test_handle_stage_callback_last_stage_completes_job():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job=JOB)
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.update_job", AsyncMock()) as mock_update:
        await handle_stage_callback(pool, redis, "job123", "summarize", "success", 0, None)
    mock_update.assert_called()
    last_call = mock_update.call_args_list[-1]
    assert last_call[1]["status"] == "completed"


@pytest.mark.asyncio
async def test_handle_stage_callback_mid_stage_stays_processing():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job=JOB)
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.upsert_job", AsyncMock()) as mock_upsert:
        await handle_stage_callback(pool, redis, "job123", "transcribe", "success", 0, None)
    statuses = [c[1]["status"] for c in mock_upsert.call_args_list if "status" in c[1]]
    assert "completed" not in statuses


@pytest.mark.asyncio
async def test_handle_stage_callback_failure_retries():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job=JOB)
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.upsert_job", AsyncMock()), \
         patch("api.services.dag._enqueue_after_backoff") as mock_backoff:
        await handle_stage_callback(pool, redis, "job123", "transcribe", "failed", 0, "OOM")
    mock_backoff.assert_called_once()


@pytest.mark.asyncio
async def test_handle_stage_callback_failure_final_fail():
    from api.services.dag import handle_stage_callback
    pool = _make_pool(job={**JOB, "retry_count": 3})
    redis = _make_redis()
    with patch("api.services.dag.get_dag_flow", AsyncMock(return_value=FLOW)), \
         patch("api.services.dag.insert_event", AsyncMock()), \
         patch("api.services.dag.update_job", AsyncMock()) as mock_update, \
         patch("api.services.dag._enqueue_after_backoff") as mock_backoff:
        await handle_stage_callback(pool, redis, "job123", "transcribe", "failed", 3, "OOM")
    mock_backoff.assert_not_called()
    statuses = [c[1]["status"] for c in mock_update.call_args_list if "status" in c[1]]
    assert "failed" in statuses
