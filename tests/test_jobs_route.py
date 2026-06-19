"""Tests for GET /jobs and GET /jobs/{id} routes."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.routes import jobs as jobs_router
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.state.pool = MagicMock()
    return TestClient(app)


def test_get_job_returns_job_dict(client):
    fake_job = {
        "id": "job1", "filename": "test.m4a", "status": "completed",
        "submitted_by": "anonymous", "dag_flow_id": "general-v1",
        "current_stage": "summarize", "submitted_at": 1000.0,
        "started_at": 1001.0, "completed_at": 1010.0, "retry_count": 0,
        "error_msg": None, "output_srt_path": None,
        "corrected_srt_path": None, "verification_status": "unverified",
        "verified_at": None, "verified_by": None,
        "minio_input_key": "input/test.m4a",
        "minio_processing_key": "processing/job1/test.wav",
    }
    with patch("api.db.get_job", AsyncMock(return_value=fake_job)):
        resp = client.get("/jobs/job1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "job1"


def test_get_job_not_found(client):
    with patch("api.db.get_job", AsyncMock(return_value=None)):
        resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404


def test_list_jobs_returns_overview(client):
    fake_overview = {
        "processing": [],
        "queue": [{"id": "job2", "status": "submitted"}],
        "recent": [],
        "failed": [],
    }
    with patch("api.db.get_status_overview", AsyncMock(return_value=fake_overview)):
        resp = client.get("/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "processing" in data
    assert "queue" in data
    assert len(data["queue"]) == 1
