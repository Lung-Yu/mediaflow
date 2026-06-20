"""Tests for upload API routes."""
import asyncio
import math
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())


@pytest.fixture
def mock_minio():
    m = MagicMock()
    m.create_multipart_upload.return_value = "test-upload-id"
    m.presign_part_urls.return_value = [
        {"part_number": 1, "url": "http://minio/part1"},
        {"part_number": 2, "url": "http://minio/part2"},
    ]
    with patch("api.utils.minio.get_client", return_value=m):
        yield m


@pytest.fixture
def client(mock_minio):
    from fastapi import FastAPI
    from api.routes import upload as upload_mod
    import importlib
    importlib.reload(upload_mod)
    app = FastAPI()
    app.include_router(upload_mod.router)
    return TestClient(app)


def test_upload_init_returns_presigned_parts(client, mock_minio):
    resp = client.post("/upload/init", json={
        "filename": "lecture.mp4",
        "size_bytes": 200 * 1024 * 1024,  # 200 MB → 2 parts
        "content_type": "video/mp4",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["upload_id"] == "test-upload-id"
    assert data["stem"] == "lecture"
    assert data["minio_key"] == "lecture/lecture.mp4"
    assert data["part_size"] == 100 * 1024 * 1024
    assert len(data["parts"]) == 2


def test_upload_init_rejects_oversized_file(client, mock_minio, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_FILE_BYTES", str(100 * 1024 * 1024))
    import api.routes.upload as upload_mod
    upload_mod.MAX_FILE_BYTES = 100 * 1024 * 1024
    resp = client.post("/upload/init", json={
        "filename": "huge.mp4",
        "size_bytes": 200 * 1024 * 1024,
    })
    assert resp.status_code == 400


def test_upload_init_stem_sanitisation(client, mock_minio):
    resp = client.post("/upload/init", json={
        "filename": "My Lecture 01.mp4",
        "size_bytes": 50 * 1024 * 1024,
    })
    assert resp.status_code == 200
    assert resp.json()["stem"] == "my_lecture_01"


def test_upload_init_rejects_duplicate_pending(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("lecture", status="processing", filename="lecture.mp4")
    )
    resp = client.post("/upload/init", json={
        "filename": "lecture.mp4",
        "size_bytes": 50 * 1024 * 1024,
    })
    assert resp.status_code == 409


def test_upload_init_allows_retry_after_failure(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("lecture", status="failed", filename="lecture.mp4")
    )
    resp = client.post("/upload/init", json={
        "filename": "lecture.mp4",
        "size_bytes": 50 * 1024 * 1024,
    })
    assert resp.status_code == 200


def test_upload_complete_creates_pending_task(client, mock_minio):
    resp = client.post("/upload/complete", json={
        "upload_id": "test-upload-id",
        "minio_key": "lecture/lecture.mp4",
        "parts": [
            {"part_number": 1, "etag": '"abc"'},
            {"part_number": 2, "etag": '"def"'},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["stem"] == "lecture"
    assert data["status"] == "pending"
    mock_minio.complete_multipart_upload.assert_called_once()


def test_upload_complete_stores_minio_key(client, mock_minio):
    import api.db as db_mod
    client.post("/upload/complete", json={
        "upload_id": "uid",
        "minio_key": "lecture/lecture.mp4",
        "parts": [{"part_number": 1, "etag": '"abc"'}],
    })
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("lecture"))
    assert task["minio_input_key"] == "lecture/lecture.mp4"
    assert task["status"] == "pending"


def test_get_queue_returns_upload_tasks(client, mock_minio):
    import api.db as db_mod
    mock_minio.presign_get_url.return_value = "http://minio/dl"
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s1", status="pending", minio_input_key="s1/f.mp4",
                            filename="f.mp4", submitted_at=1000.0)
    )
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s2", status="completed", filename="g.mp4")  # no minio key
    )
    resp = client.get("/upload/queue")
    assert resp.status_code == 200
    stems = [t["stem"] for t in resp.json()]
    assert "s1" in stems
    assert "s2" not in stems


def test_get_queue_includes_download_urls_for_completed(client, mock_minio):
    import api.db as db_mod
    mock_minio.presign_get_url.return_value = "http://minio/download.srt"
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("done_stem", status="completed",
                            minio_input_key="done_stem/f.mp4", filename="f.mp4",
                            minio_output_prefix="done_stem/")
    )
    resp = client.get("/upload/queue")
    task = next(t for t in resp.json() if t["stem"] == "done_stem")
    assert ".srt" in task["downloads"]


def test_delete_queue_cancels_pending(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("to_cancel", status="pending",
                            minio_input_key="to_cancel/f.mp4", filename="f.mp4")
    )
    resp = client.delete("/upload/queue/to_cancel")
    assert resp.status_code == 200
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("to_cancel"))
    assert task is None


def test_delete_queue_rejects_active_task(client, mock_minio):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("active", status="processing",
                            minio_input_key="active/f.mp4", filename="f.mp4")
    )
    resp = client.delete("/upload/queue/active")
    assert resp.status_code == 409


def test_delete_queue_404_for_missing(client, mock_minio):
    resp = client.delete("/upload/queue/ghost")
    assert resp.status_code == 404
