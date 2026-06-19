"""Tests for api/services/project.py — FR6 validation and create_job intake flow."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.services.project import validate_fr6

MAX = 5 * 1024 * 1024 * 1024  # 5 GB


def test_valid_filename_passes():
    ok, reason = validate_fr6("lesson01.m4a", 1024 * 1024, MAX)
    assert ok is True
    assert reason == ""


def test_empty_file_fails():
    ok, reason = validate_fr6("lesson01.m4a", 0, MAX)
    assert ok is False
    assert "empty" in reason.lower()


def test_oversized_file_fails():
    ok, reason = validate_fr6("lesson01.m4a", MAX + 1, MAX)
    assert ok is False
    assert "size" in reason.lower()


def test_path_traversal_fails():
    ok, reason = validate_fr6("../../../etc/passwd", 1024, MAX)
    assert ok is False
    assert "traversal" in reason.lower() or "path" in reason.lower()


def test_null_byte_in_name_fails():
    ok, reason = validate_fr6("lesson\x00.m4a", 1024, MAX)
    assert ok is False
    assert "null" in reason.lower() or "character" in reason.lower()


def test_very_long_filename_fails():
    ok, reason = validate_fr6("a" * 256 + ".m4a", 1024, MAX)
    assert ok is False
    assert "long" in reason.lower() or "length" in reason.lower()


def test_chinese_filename_passes():
    ok, reason = validate_fr6("第一課_錄音.m4a", 1024 * 1024, MAX)
    assert ok is True


def test_unsupported_extension_fails():
    ok, reason = validate_fr6("lesson.exe", 1024, MAX)
    assert ok is False
    assert "extension" in reason.lower() or "format" in reason.lower()


# ── create_job tests ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.xadd = AsyncMock(return_value="1234-0")
    return r


@pytest.fixture
def mock_minio():
    m = MagicMock()
    m.input_bucket = "mediaflow-input"
    m._s3 = MagicMock()
    m._s3.head_object.return_value = {"ContentLength": 1024 * 1024}
    m.copy_input_to_processing.return_value = "processing/job1/lesson01.m4a"
    return m


@pytest.mark.asyncio
async def test_create_job_happy_path(mock_pool, mock_redis, mock_minio):
    from api.services.project import create_job
    mock_pool.fetchrow.return_value = {
        "id": "general-v1", "stage_plan": [{"stage": "preprocess", "config": {}}],
        "is_default": True, "deprecated": False, "created_at": 1000.0,
    }

    with patch("api.services.dag.trigger_job", AsyncMock()):
        job_id = await create_job(
            pool=mock_pool, redis=mock_redis, minio_client=mock_minio,
            input_key="input/lesson01.m4a", dag_flow=None, max_file_bytes=5 * 1024 ** 3,
        )
    assert isinstance(job_id, str)
    assert len(job_id) > 0
    assert mock_minio.copy_input_to_processing.called


@pytest.mark.asyncio
async def test_create_job_file_not_in_minio_raises(mock_pool, mock_redis, mock_minio):
    from api.services.project import create_job
    from botocore.exceptions import ClientError
    mock_minio._s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    with pytest.raises(FileNotFoundError, match="MinIO"):
        await create_job(
            pool=mock_pool, redis=mock_redis, minio_client=mock_minio,
            input_key="input/missing.m4a", dag_flow=None, max_file_bytes=5 * 1024 ** 3,
        )


@pytest.mark.asyncio
async def test_create_job_fr6_failure_raises(mock_pool, mock_redis, mock_minio):
    from api.services.project import create_job
    mock_minio._s3.head_object.return_value = {"ContentLength": 0}  # empty file
    with pytest.raises(ValueError, match="FR6"):
        await create_job(
            pool=mock_pool, redis=mock_redis, minio_client=mock_minio,
            input_key="input/empty.m4a", dag_flow=None, max_file_bytes=5 * 1024 ** 3,
        )
    # Ensure no processing copy was made
    assert not mock_minio.copy_input_to_processing.called
