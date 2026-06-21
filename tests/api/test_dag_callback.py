"""Tests for POST /internal/stage-callback route."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_client():
    from api.routes import dag_callback
    app = FastAPI()
    app.include_router(dag_callback.router)
    app.state.pool = MagicMock()
    app.state.redis = MagicMock()
    return TestClient(app)


def test_stage_callback_success():
    client = _make_client()
    with patch("api.routes.dag_callback.handle_stage_callback", AsyncMock()):
        resp = client.post("/internal/stage-callback", json={
            "job_id": "job1", "stage": "transcribe",
            "status": "success", "retry_attempt": 0,
        })
    assert resp.status_code == 204


def test_stage_callback_failure():
    client = _make_client()
    with patch("api.routes.dag_callback.handle_stage_callback", AsyncMock()):
        resp = client.post("/internal/stage-callback", json={
            "job_id": "job1", "stage": "transcribe",
            "status": "failed", "retry_attempt": 0, "error_msg": "timeout",
        })
    assert resp.status_code == 204


def test_stage_callback_missing_fields_returns_422():
    client = _make_client()
    resp = client.post("/internal/stage-callback", json={"job_id": "job1"})
    assert resp.status_code == 422
