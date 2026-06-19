"""Tests: /partial/jobs HTMX partial renders new v2 job shape."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.main import app
    return TestClient(app)


def test_dashboard_renders_processing_jobs(client):
    fake_overview = {
        "processing": [{"id": "job1", "filename": "lesson01.m4a",
                        "status": "processing", "current_stage": "transcribe",
                        "submitted_at": 1000.0, "retry_count": 0}],
        "queue":  [],
        "recent": [],
        "failed": [],
    }
    with patch("httpx.AsyncClient.get", AsyncMock(
        return_value=type("R", (), {"json": lambda s: fake_overview, "status_code": 200})()
    )):
        resp = client.get("/partial/jobs")
    assert resp.status_code == 200
    assert "lesson01.m4a" in resp.text
    assert "processing" in resp.text


def test_dashboard_renders_failed_jobs(client):
    fake_overview = {
        "processing": [],
        "queue":  [],
        "recent": [],
        "failed": [{"id": "job2", "filename": "bad.m4a",
                    "status": "failed", "error_msg": "ffmpeg error",
                    "current_stage": "preprocess", "submitted_at": 999.0, "retry_count": 3}],
    }
    with patch("httpx.AsyncClient.get", AsyncMock(
        return_value=type("R", (), {"json": lambda s: fake_overview, "status_code": 200})()
    )):
        resp = client.get("/partial/jobs")
    assert "bad.m4a" in resp.text
    assert "ffmpeg error" in resp.text


def test_dashboard_renders_empty_state(client):
    fake_overview = {"processing": [], "queue": [], "recent": [], "failed": []}
    with patch("httpx.AsyncClient.get", AsyncMock(
        return_value=type("R", (), {"json": lambda s: fake_overview, "status_code": 200})()
    )):
        resp = client.get("/partial/jobs")
    assert resp.status_code == 200


def test_dashboard_renders_queued_jobs(client):
    fake_overview = {
        "processing": [],
        "queue": [{"id": "job3", "filename": "pending.m4a", "status": "submitted",
                   "current_stage": None, "submitted_at": 1100.0, "retry_count": 0}],
        "recent": [],
        "failed": [],
    }
    with patch("httpx.AsyncClient.get", AsyncMock(
        return_value=type("R", (), {"json": lambda s: fake_overview, "status_code": 200})()
    )):
        resp = client.get("/partial/jobs")
    assert "pending.m4a" in resp.text


def test_upload_complete_propagates_job_creation_failure(client):
    """POST /upload/complete must return an error when POST /jobs returns non-201."""
    complete_resp = type("R", (), {
        "json": lambda s: {"minio_key": "input/test.m4a"},
        "status_code": 200,
    })()
    job_fail_resp = type("R", (), {"status_code": 400, "text": "file too large"})()

    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=[complete_resp, job_fail_resp])):
        resp = client.post("/upload/complete", json={"minio_key": "input/test.m4a"})

    assert resp.status_code == 400
