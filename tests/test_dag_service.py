"""Tests for DAG service — trigger_job, handle_stage_callback, retry logic."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.xadd = AsyncMock(return_value="1234-0")
    r.aclose = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_app_state_has_redis_after_lifespan(mock_pool, mock_redis):
    with patch("asyncpg.create_pool", AsyncMock(return_value=mock_pool)), \
         patch("redis.asyncio.from_url", return_value=mock_redis), \
         patch("api.db.init", AsyncMock()), \
         patch("api.main.reconcile", AsyncMock()), \
         patch("api.minio_client.init_client"), \
         patch("api.minio_client.get_client", return_value=MagicMock()), \
         patch("api.cleanup.cleanup_loop", AsyncMock()), \
         patch("api.mq.consumer.run", AsyncMock()), \
         patch("api.mq.queue_consumer.run", AsyncMock()):
        from api.main import app, lifespan
        async with lifespan(app):
            assert app.state.redis is mock_redis


@pytest.mark.asyncio
async def test_trigger_job_enqueues_to_mq(mock_pool, mock_redis):
    from api.services.dag import trigger_job

    mock_pool.fetchrow.return_value = {
        "id": "general-v1",
        "stage_plan": [
            {"stage": "preprocess", "config": {"provider": "ffmpeg"}},
            {"stage": "transcribe", "config": {"provider": "mlx-whisper"}},
        ],
        "is_default": True, "deprecated": False, "created_at": 1000.0,
    }
    mock_pool.execute.return_value = None

    await trigger_job(
        mock_pool, mock_redis,
        job_id="job1", flow_id=None,
        minio_processing_key="processing/job1/test.wav",
    )

    assert mock_redis.xadd.called
    stream, fields = mock_redis.xadd.call_args[0]
    assert stream == "mediaflow:jobs"
    assert fields["job_id"] == "job1"
    assert fields["resume_from_stage"] == "preprocess"
    stage_plan = json.loads(fields["stage_plan"])
    assert stage_plan[0]["stage"] == "preprocess"


@pytest.mark.asyncio
async def test_trigger_job_sets_status_queued(mock_pool, mock_redis):
    from api.services.dag import trigger_job

    mock_pool.fetchrow.return_value = {
        "id": "general-v1",
        "stage_plan": [{"stage": "preprocess", "config": {}}],
        "is_default": True, "deprecated": False, "created_at": 1000.0,
    }
    mock_pool.execute.return_value = None

    await trigger_job(
        mock_pool, mock_redis,
        job_id="job1", flow_id=None,
        minio_processing_key="processing/job1/test.wav",
    )

    all_args = []
    for call in mock_pool.execute.call_args_list:
        all_args.extend(call[0])  # positional args to each execute call

    assert "queued" in all_args, f"Expected 'queued' in execute args, got: {all_args}"


@pytest.mark.asyncio
async def test_handle_stage_callback_success_records_event(mock_pool, mock_redis):
    from api.services.dag import handle_stage_callback

    mock_pool.execute.return_value = None

    await handle_stage_callback(
        mock_pool, mock_redis,
        job_id="job1", stage="transcribe", status="success",
        retry_attempt=0, error_msg=None,
    )

    assert mock_pool.execute.called
    sql_calls = [str(c[0][0]) for c in mock_pool.execute.call_args_list]
    event_sql = [s for s in sql_calls if "INSERT INTO events" in s]
    assert event_sql, "Expected INSERT INTO events call"


@pytest.mark.asyncio
async def test_handle_stage_callback_failed_schedules_retry(mock_pool, mock_redis):
    from api.services.dag import handle_stage_callback

    mock_pool.execute.return_value = None
    mock_pool.fetchrow.return_value = {
        "id": "job1", "retry_count": 0, "minio_processing_key": "processing/job1/test.wav",
        "dag_flow_id": "general-v1", "filename": "test.m4a", "status": "processing",
        "submitted_by": "anonymous", "current_stage": "transcribe",
        "submitted_at": 1000.0, "started_at": 1001.0, "completed_at": None,
        "error_msg": None, "output_srt_path": None, "corrected_srt_path": None,
        "verification_status": "unverified", "verified_at": None, "verified_by": None,
        "minio_input_key": "input/test.m4a",
    }

    with patch("api.services.dag._retry_after", AsyncMock()) as mock_retry, \
         patch("asyncio.create_task") as mock_create_task:
        await handle_stage_callback(
            mock_pool, mock_redis,
            job_id="job1", stage="transcribe", status="failed",
            retry_attempt=0, error_msg="timeout",
        )
        assert mock_create_task.called
        mock_retry.assert_called_once()
        call_kwargs = mock_retry.call_args
        assert "transcribe" in str(call_kwargs)


@pytest.mark.asyncio
async def test_handle_stage_callback_failed_max_retries_sets_failed(mock_pool, mock_redis):
    from api.services.dag import handle_stage_callback, MAX_RETRIES

    mock_pool.execute.return_value = None
    mock_pool.fetchrow.return_value = {
        "id": "job1", "retry_count": MAX_RETRIES,
        "minio_processing_key": "processing/job1/test.wav",
        "dag_flow_id": "general-v1", "filename": "test.m4a", "status": "processing",
        "submitted_by": "anonymous", "current_stage": "transcribe",
        "submitted_at": 1000.0, "started_at": 1001.0, "completed_at": None,
        "error_msg": None, "output_srt_path": None, "corrected_srt_path": None,
        "verification_status": "unverified", "verified_at": None, "verified_by": None,
        "minio_input_key": "input/test.m4a",
    }

    await handle_stage_callback(
        mock_pool, mock_redis,
        job_id="job1", stage="transcribe", status="failed",
        retry_attempt=MAX_RETRIES, error_msg="persistent error",
    )

    all_args = [arg for c in mock_pool.execute.call_args_list for arg in c[0]]
    assert "failed" in all_args
