import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import time

# We test queries.py by injecting a mock pool
# pool.fetchrow(), pool.execute(), pool.fetch() are AsyncMock

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
    return pool

@pytest.mark.asyncio
async def test_upsert_job_inserts_new(mock_pool):
    from api.db.queries import upsert_job
    mock_pool.execute.return_value = None
    await upsert_job(mock_pool, "job1", filename="test.m4a", status="submitted")
    assert mock_pool.execute.called
    sql, *args = mock_pool.execute.call_args[0]
    assert "INSERT INTO jobs" in sql
    assert "job1" in args

@pytest.mark.asyncio
async def test_get_job_returns_dict(mock_pool):
    from api.db.queries import get_job
    mock_pool.fetchrow.return_value = {
        "id": "job1", "filename": "test.m4a", "status": "completed",
        "submitted_by": "anonymous", "dag_flow_id": "general-v1",
        "current_stage": None, "submitted_at": 1000.0, "started_at": 1001.0,
        "completed_at": 1010.0, "retry_count": 0, "error_msg": None,
        "output_srt_path": None, "corrected_srt_path": None,
        "verification_status": "unverified", "verified_at": None,
        "verified_by": None, "minio_input_key": None, "minio_processing_key": None,
    }
    result = await get_job(mock_pool, "job1")
    assert result["id"] == "job1"
    assert result["status"] == "completed"

@pytest.mark.asyncio
async def test_get_job_not_found_returns_none(mock_pool):
    from api.db.queries import get_job
    mock_pool.fetchrow.return_value = None
    result = await get_job(mock_pool, "nonexistent")
    assert result is None

@pytest.mark.asyncio
async def test_count_active_jobs(mock_pool):
    from api.db.queries import count_active_jobs
    mock_pool.fetchrow.return_value = {"count": 3}
    result = await count_active_jobs(mock_pool)
    assert result == 3

@pytest.mark.asyncio
async def test_insert_event(mock_pool):
    from api.db.queries import insert_event
    await insert_event(mock_pool, "job1", stage="transcribe", status="success",
                       retry_attempt=0, ts=time.time())
    assert mock_pool.execute.called
    sql, *_ = mock_pool.execute.call_args[0]
    assert "INSERT INTO events" in sql

@pytest.mark.asyncio
async def test_get_dag_flow_returns_default_when_none(mock_pool):
    from api.db.queries import get_dag_flow
    mock_pool.fetchrow.return_value = {
        "id": "general-v1",
        "stage_plan": [{"stage": "preprocess", "config": {"provider": "ffmpeg"}}],
        "is_default": True,
        "deprecated": False,
        "created_at": 1000.0,
    }
    result = await get_dag_flow(mock_pool, None)
    assert result["id"] == "general-v1"
    sql, *_ = mock_pool.fetchrow.call_args[0]
    assert "is_default = true" in sql

@pytest.mark.asyncio
async def test_get_dag_flow_by_id(mock_pool):
    from api.db.queries import get_dag_flow
    mock_pool.fetchrow.return_value = {
        "id": "course-v1",
        "stage_plan": [],
        "is_default": False,
        "deprecated": False,
        "created_at": 1000.0,
    }
    result = await get_dag_flow(mock_pool, "course-v1")
    assert result["id"] == "course-v1"
    sql, *args = mock_pool.fetchrow.call_args[0]
    assert "course-v1" in args
