"""Tests: SRT viewer play buttons + /jobs/{id}/segment/{index}/play-url proxy."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.main import app
    return TestClient(app)


def test_srt_viewer_has_play_buttons(client):
    """Each segment row in the SRT viewer has a play control."""
    fake_srt = (
        "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n"
        "2\n00:00:02,100 --> 00:00:04,000\n再見\n"
    )
    fake_job = {"id": "job1", "filename": "lesson01.m4a", "status": "completed"}
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            text=fake_srt, json=MagicMock(return_value=fake_job), status_code=200
        )
        resp = client.get("/files/job1/srt")
    assert resp.status_code == 200
    assert resp.text.count("data-segment-index") >= 2


def test_segment_audio_proxy_returns_url(client):
    """GET /jobs/{id}/segment/{index}/play-url proxies presigned clip URL."""
    with patch("httpx.AsyncClient.get", AsyncMock(
        return_value=type("R", (), {
            "json": lambda s: {"url": "https://minio/clip.wav", "expires_in": 600},
            "status_code": 200
        })()
    )):
        resp = client.get("/jobs/job1/segment/0/play-url")
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://")


def test_srt_viewer_includes_audio_element(client):
    """The SRT viewer page includes the shared segment-player audio element."""
    fake_srt = "1\n00:00:00,000 --> 00:00:02,000\n測試\n"
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            text=fake_srt, json=MagicMock(return_value={}), status_code=200
        )
        resp = client.get("/files/job1/srt")
    assert "segment-player" in resp.text
    assert "playSegment" in resp.text
