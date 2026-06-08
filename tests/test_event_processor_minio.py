"""Test that task.completed triggers MinIO output backup."""
import asyncio
import os
import time
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())


@pytest.fixture
def mock_minio_client():
    m = MagicMock()
    m.upload_outputs = MagicMock()
    return m


def test_task_completed_triggers_minio_backup(mock_minio_client, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "3_output").mkdir(parents=True)

    with patch("api.minio_client.get_client", return_value=mock_minio_client), \
         patch("api.minio_client._client", mock_minio_client):

        import importlib
        import api.event_processor as ep
        importlib.reload(ep)

        asyncio.get_event_loop().run_until_complete(ep.process_event({
            "event": "task.completed",
            "stem": "lecture",
            "output_path": str(tmp_path / "3_output" / "lecture.srt"),
            "ts": str(time.time()),
        }))

    mock_minio_client.upload_outputs.assert_called_once()
    call_args = mock_minio_client.upload_outputs.call_args
    assert call_args[0][0] == "lecture"


def test_task_completed_backup_failure_does_not_raise(mock_minio_client, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "3_output").mkdir(parents=True)
    mock_minio_client.upload_outputs.side_effect = Exception("MinIO down")

    with patch("api.minio_client.get_client", return_value=mock_minio_client), \
         patch("api.minio_client._client", mock_minio_client):

        import importlib
        import api.event_processor as ep
        importlib.reload(ep)

        # Should not raise — backup failure is non-fatal
        asyncio.get_event_loop().run_until_complete(ep.process_event({
            "event": "task.completed",
            "stem": "lecture",
            "ts": str(time.time()),
        }))
