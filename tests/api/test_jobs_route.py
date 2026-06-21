"""Tests for GET/POST/DELETE /jobs and /jobs/{id}/rerun routes."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_client():
    from api.routes import jobs as jobs_router
    app = FastAPI()
    app.include_router(jobs_router.router)
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock()
    app.state.pool = pool
    app.state.redis = AsyncMock()
    return TestClient(app), pool


def test_get_job_returns_job_dict():
    client, _ = _make_client()
    fake_job = {"id": "job1", "filename": "test.m4a", "status": "completed"}
    with patch("api.db.get_job", AsyncMock(return_value=fake_job)):
        resp = client.get("/jobs/job1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "job1"


def test_get_job_not_found():
    client, _ = _make_client()
    with patch("api.db.get_job", AsyncMock(return_value=None)):
        resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404


def test_list_jobs_returns_overview():
    client, _ = _make_client()
    overview = {"processing": [], "queue": [], "recent": [], "failed": []}
    with patch("api.db.get_status_overview", AsyncMock(return_value=overview)):
        resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "processing" in resp.json()


def test_post_job_creates_job():
    client, _ = _make_client()
    with patch("api.utils.minio.get_client", return_value=MagicMock()), \
         patch("api.routes.jobs.on_upload_trigger", AsyncMock(return_value="job-abc")):
        resp = client.post("/jobs", json={"file_key": "input/lesson.m4a", "filename": "lesson.m4a"})
    assert resp.status_code == 201
    assert resp.json()["job_id"] == "job-abc"


def test_delete_job_removes_record():
    client, pool = _make_client()
    fake_job = {"id": "job1", "filename": "test.m4a", "status": "queued"}
    with patch("api.db.get_job", AsyncMock(return_value=fake_job)):
        resp = client.delete("/jobs/job1")
    assert resp.status_code == 204
    assert pool.execute.call_count == 2  # DELETE events + DELETE jobs


def test_delete_job_not_found():
    client, _ = _make_client()
    with patch("api.db.get_job", AsyncMock(return_value=None)):
        resp = client.delete("/jobs/missing")
    assert resp.status_code == 404


def test_rerun_job_triggers_dag():
    client, _ = _make_client()
    fake_job = {
        "id": "job1", "filename": "test.m4a", "status": "failed",
        "minio_processing_key": "processing/job1/test.m4a",
        "dag_flow_id": "general-v1",
    }
    with patch("api.db.get_job", AsyncMock(return_value=fake_job)), \
         patch("api.routes.jobs.check_capacity", AsyncMock()), \
         patch("api.routes.jobs.trigger_job", AsyncMock()):
        resp = client.post("/jobs/job1/rerun")
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"


def test_rerun_job_no_processing_key():
    client, _ = _make_client()
    fake_job = {"id": "job1", "filename": "test.m4a", "status": "failed",
                "minio_processing_key": None, "dag_flow_id": None}
    with patch("api.db.get_job", AsyncMock(return_value=fake_job)):
        resp = client.post("/jobs/job1/rerun")
    assert resp.status_code == 409
