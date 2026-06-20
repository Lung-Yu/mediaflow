"""Tests for GET /jobs/{id}/segment/{index}/audio — segment audio clip endpoint."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 2.5, "text": "你好"},
    {"id": 1, "start": 2.6, "end": 5.0, "text": "再見"},
]

FAKE_JOB = {
    "id": "job1", "filename": "lesson01.m4a",
    "status": "completed", "verification_status": "unverified",
}


@pytest.fixture
def client():
    from api.routes import clip as clip_router
    app = FastAPI()
    app.include_router(clip_router.router)
    app.state.pool = MagicMock()
    return TestClient(app)


def _mock_minio_with_segments(segments):
    m = MagicMock()
    m.output_bucket = "mediaflow-output"
    m.clips_bucket  = "mediaflow-clips"
    m._s3 = MagicMock()
    m._s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(segments).encode()))
    }
    # Simulate clip already cached (head_object succeeds)
    m._s3.head_object.return_value = {"ContentLength": 50000}
    m.presign_get_url.return_value = "https://minio/clips/job1/0.wav?presign=abc"
    return m


def test_get_segment_audio_cached(client):
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    with patch("api.utils.minio.get_client", return_value=mock_minio), \
         patch("api.db.get_job", AsyncMock(return_value=FAKE_JOB)):
        resp = client.get("/jobs/job1/segment/0/audio")
    assert resp.status_code == 200
    assert "url" in resp.json()
    assert resp.json()["expires_in"] == 600


def test_get_segment_audio_cache_miss_generates_clip(client):
    from botocore.exceptions import ClientError
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    # head_object raises → cache miss
    mock_minio._s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": ""}}, "HeadObject"
    )
    mock_minio._s3.upload_fileobj = MagicMock()
    with patch("api.utils.minio.get_client", return_value=mock_minio), \
         patch("api.db.get_job", AsyncMock(return_value=FAKE_JOB)), \
         patch("api.routes.clip._generate_clip_bytes",
               AsyncMock(return_value=b"fake-wav")):
        resp = client.get("/jobs/job1/segment/0/audio")
    assert resp.status_code == 200
    assert mock_minio._s3.upload_fileobj.called


def test_get_segment_audio_invalid_index(client):
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    with patch("api.utils.minio.get_client", return_value=mock_minio), \
         patch("api.db.get_job", AsyncMock(return_value=FAKE_JOB)):
        resp = client.get("/jobs/job1/segment/99/audio")
    assert resp.status_code == 404


def test_get_segment_audio_job_not_found(client):
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    with patch("api.utils.minio.get_client", return_value=mock_minio), \
         patch("api.db.get_job", AsyncMock(return_value=None)):
        resp = client.get("/jobs/missing/segment/0/audio")
    assert resp.status_code == 404


def test_get_segment_audio_returns_start_end(client):
    mock_minio = _mock_minio_with_segments(SEGMENTS)
    with patch("api.utils.minio.get_client", return_value=mock_minio), \
         patch("api.db.get_job", AsyncMock(return_value=FAKE_JOB)):
        resp = client.get("/jobs/job1/segment/1/audio")
    data = resp.json()
    assert data["start"] == 2.6
    assert data["end"] == 5.0
