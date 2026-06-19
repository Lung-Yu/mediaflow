"""Tests for api/services/correction.py and correction routes."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 2.0, "text": "你好"},
    {"id": 1, "start": 2.1, "end": 4.0, "text": "我是老師"},
    {"id": 2, "start": 4.1, "end": 6.0, "text": "再見"},
]


@pytest.fixture
def mock_pool():
    p = MagicMock()
    p.fetchrow = AsyncMock()
    p.execute  = AsyncMock()
    return p


@pytest.fixture
def mock_minio():
    m = MagicMock()
    m.output_bucket = "mediaflow-output"
    m._s3 = MagicMock()
    m._s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(SEGMENTS).encode()))
    }
    m._s3.put_object = MagicMock()
    return m


# ── Service unit tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_correction_produces_valid_srt(mock_pool, mock_minio):
    from api.services.correction import apply_correction
    mock_pool.fetchrow.return_value = {
        "id": "job1", "filename": "lesson01.m4a",
        "verification_status": "unverified",
        "corrected_srt_path": None,
    }

    edits = [{"index": 0, "text": "你好啊"}]  # correct segment 0
    with patch("api.db.get_job", AsyncMock(return_value=mock_pool.fetchrow.return_value)), \
         patch("api.db.upsert_job", AsyncMock()):
        srt = await apply_correction(mock_pool, mock_minio, "job1", edits)

    # SRT should contain edited text
    assert "你好啊" in srt
    # SRT should contain unedited segments too
    assert "我是老師" in srt
    assert "再見" in srt
    # put_object must have been called to write corrected SRT to MinIO
    assert mock_minio._s3.put_object.called


@pytest.mark.asyncio
async def test_apply_correction_sets_in_progress_from_unverified(mock_pool, mock_minio):
    from api.services.correction import apply_correction
    job = {"id": "job1", "filename": "lesson01.m4a",
           "verification_status": "unverified", "corrected_srt_path": None}
    upsert_calls = []
    with patch("api.db.get_job", AsyncMock(return_value=job)), \
         patch("api.db.upsert_job", AsyncMock(side_effect=lambda pool, jid, **kw: upsert_calls.append(kw))):
        await apply_correction(mock_pool, mock_minio, "job1", [{"index": 0, "text": "test"}])

    # verification_status must have been set to in_progress
    vs_calls = [c for c in upsert_calls if "verification_status" in c]
    assert any(c["verification_status"] == "in_progress" for c in vs_calls)


@pytest.mark.asyncio
async def test_apply_correction_leaves_status_when_in_progress(mock_pool, mock_minio):
    from api.services.correction import apply_correction
    job = {"id": "job1", "filename": "lesson01.m4a",
           "verification_status": "in_progress", "corrected_srt_path": "output/job1/lesson01_corrected.srt"}
    upsert_calls = []
    with patch("api.db.get_job", AsyncMock(return_value=job)), \
         patch("api.db.upsert_job", AsyncMock(side_effect=lambda pool, jid, **kw: upsert_calls.append(kw))):
        await apply_correction(mock_pool, mock_minio, "job1", [{"index": 0, "text": "test"}])

    # verification_status must NOT have changed (already in_progress)
    vs_calls = [c for c in upsert_calls if "verification_status" in c]
    assert not any(c.get("verification_status") == "in_progress" for c in vs_calls)


@pytest.mark.asyncio
async def test_finalize_sets_verified(mock_pool):
    from api.services.correction import finalize_verification
    upsert_calls = []
    with patch("api.db.upsert_job", AsyncMock(side_effect=lambda pool, jid, **kw: upsert_calls.append(kw))):
        await finalize_verification(mock_pool, "job1")
    assert any(c.get("verification_status") == "verified" for c in upsert_calls)
    assert any("verified_at" in c for c in upsert_calls)


# ── Route integration tests ───────────────────────────────────────────────────

@pytest.fixture
def client():
    from api.routes import correction as correction_router
    app = FastAPI()
    app.include_router(correction_router.router)
    app.state.pool = MagicMock()
    return TestClient(app)


def test_patch_correction_returns_200(client):
    with patch("api.minio_client.get_client", return_value=MagicMock()), \
         patch("api.services.correction.apply_correction", AsyncMock(return_value="1\n...")):
        resp = client.patch("/jobs/job1/correction",
                            json={"segments": [{"index": 0, "text": "你好"}]})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_finalize_returns_200(client):
    with patch("api.services.correction.finalize_verification", AsyncMock()):
        resp = client.post("/jobs/job1/correction/finalize")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_patch_correction_invalid_body_returns_422(client):
    resp = client.patch("/jobs/job1/correction", json={"bad_field": 1})
    assert resp.status_code == 422


def test_patch_correction_job_not_found_returns_404(client):
    with patch("api.minio_client.get_client", return_value=MagicMock()), \
         patch("api.services.correction.apply_correction",
               AsyncMock(side_effect=ValueError("Job 'missing' not found"))):
        resp = client.patch("/jobs/missing/correction",
                            json={"segments": [{"index": 0, "text": "test"}]})
    assert resp.status_code == 404
