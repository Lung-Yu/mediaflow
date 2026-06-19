"""Tests for POST /internal/stage-callback route."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.routes import dag_callback
    app = FastAPI()
    app.include_router(dag_callback.router)
    app.state.pool = MagicMock()
    app.state.redis = MagicMock()
    return TestClient(app)


def test_stage_callback_success(client):
    with patch("api.services.dag.handle_stage_callback", AsyncMock()) as mock_cb:
        resp = client.post("/internal/stage-callback", json={
            "job_id": "job1",
            "stage": "transcribe",
            "status": "success",
            "retry_attempt": 0,
            "error_msg": None,
        })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert mock_cb.called


def test_stage_callback_failure_triggers_retry(client):
    with patch("api.services.dag.handle_stage_callback", AsyncMock()) as mock_cb:
        resp = client.post("/internal/stage-callback", json={
            "job_id": "job1",
            "stage": "transcribe",
            "status": "failed",
            "retry_attempt": 0,
            "error_msg": "timeout",
        })
    assert resp.status_code == 200
    assert mock_cb.called


def test_stage_callback_invalid_status_rejected(client):
    resp = client.post("/internal/stage-callback", json={
        "job_id": "job1",
        "stage": "transcribe",
        "status": "unknown_status",
        "retry_attempt": 0,
    })
    assert resp.status_code == 422
