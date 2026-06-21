import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_validate_fr6_accepts_normal():
    from api.services.project import validate_fr6
    assert validate_fr6("recording.m4a", 1024 * 1024) is None


def test_validate_fr6_rejects_path_traversal():
    from api.services.project import validate_fr6
    assert validate_fr6("../etc/passwd.m4a", 1024) is not None


def test_validate_fr6_rejects_null_byte():
    from api.services.project import validate_fr6
    assert validate_fr6("file\x00.m4a", 1024) is not None


def test_validate_fr6_rejects_empty_file():
    from api.services.project import validate_fr6
    assert validate_fr6("recording.m4a", 0) is not None


def test_validate_fr6_rejects_too_large():
    from api.services.project import validate_fr6
    assert validate_fr6("recording.m4a", 6 * 1024 ** 3) is not None


def test_validate_fr6_rejects_long_filename():
    from api.services.project import validate_fr6
    assert validate_fr6("a" * 256 + ".m4a", 1024) is not None


@pytest.mark.asyncio
async def test_on_upload_trigger_creates_job():
    from api.services.project import on_upload_trigger
    pool = AsyncMock()
    redis = AsyncMock()
    minio = MagicMock()
    minio.head_object = MagicMock(return_value={"ContentLength": 1024})

    with patch("api.services.project.trigger_job", AsyncMock()) as mock_trigger, \
         patch("api.services.project.upsert_job", AsyncMock()), \
         patch("api.services.project.check_capacity", AsyncMock()):
        job_id = await on_upload_trigger(
            pool, redis, minio,
            file_key="input/test.m4a",
            filename="test.m4a",
            dag_flow_id=None,
            submitted_by="anonymous",
        )
    assert job_id is not None
    mock_trigger.assert_called_once()


@pytest.mark.asyncio
async def test_on_upload_trigger_fr6_failure_raises_http_400():
    from api.services.project import on_upload_trigger
    from fastapi import HTTPException
    pool = AsyncMock()
    redis = AsyncMock()
    minio = MagicMock()
    minio.head_object = MagicMock(return_value={"ContentLength": 0})

    with patch("api.services.project.check_capacity", AsyncMock()), \
         pytest.raises(HTTPException) as exc_info:
        await on_upload_trigger(
            pool, redis, minio,
            file_key="input/test.m4a",
            filename="test.m4a",
            dag_flow_id=None,
            submitted_by="anonymous",
        )
    assert exc_info.value.status_code == 400
