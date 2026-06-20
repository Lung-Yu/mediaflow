"""Tests for queue consumer asyncio loop."""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_db():
    with patch("api.mq.jobs_consumer.db") as m:
        m.count_active_tasks = AsyncMock(return_value=0)
        m.get_oldest_pending = AsyncMock(return_value=None)
        m.upsert_task = AsyncMock()
        yield m


@pytest.fixture
def mock_minio():
    with patch("api.mq.jobs_consumer.minio_mod") as m:
        client = MagicMock()
        client.download_to_file = MagicMock()
        m.get_client.return_value = client
        yield m, client


async def test_tick_does_nothing_when_at_capacity(mock_db, mock_minio):
    mock_db.count_active_tasks.return_value = 2
    from api.mq import jobs_consumer
    await jobs_consumer._tick()
    mock_db.get_oldest_pending.assert_not_called()


async def test_tick_does_nothing_when_no_pending(mock_db, mock_minio):
    mock_db.get_oldest_pending.return_value = None
    from api.mq import jobs_consumer
    await jobs_consumer._tick()
    mock_db.upsert_task.assert_not_called()


async def test_tick_downloads_pending_task(mock_db, mock_minio, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    mock_db.get_oldest_pending.return_value = {
        "stem": "lecture",
        "filename": "lecture.mp4",
        "minio_input_key": "lecture/lecture.mp4",
    }
    import importlib
    import api.mq.jobs_consumer as mod
    importlib.reload(mod)

    with patch.object(mod, "db", mock_db), \
         patch.object(mod, "minio_mod", mock_minio[0]):
        await mod._tick()

    status_calls = [c[1]["status"] for c in mock_db.upsert_task.call_args_list
                    if "status" in c[1]]
    assert "downloading" in status_calls
    assert "queued" in status_calls
    mock_minio[1].download_to_file.assert_called_once()


async def test_tick_sets_failed_on_download_error(mock_db, mock_minio, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    mock_db.get_oldest_pending.return_value = {
        "stem": "broken",
        "filename": "broken.mp4",
        "minio_input_key": "broken/broken.mp4",
    }
    mock_minio[1].download_to_file.side_effect = Exception("MinIO unreachable")

    import importlib
    import api.mq.jobs_consumer as mod
    importlib.reload(mod)

    with patch.object(mod, "db", mock_db), \
         patch.object(mod, "minio_mod", mock_minio[0]):
        await mod._tick()

    failed_call = next(
        (c for c in mock_db.upsert_task.call_args_list if c[1].get("status") == "failed"),
        None
    )
    assert failed_call is not None
    assert "MinIO unreachable" in failed_call[1]["error_msg"]
