from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

STAGE_PLAN = json.dumps([
    {"stage": "preprocess",  "config": {"provider": "ffmpeg"}},
    {"stage": "transcribe",  "config": {"provider": "mlx-whisper", "language": "zh"}},
    {"stage": "summarize",   "config": {"provider": "ollama", "model": "qwen2.5:7b"}},
])


def make_mq_message(resume_from="preprocess", retry=0):
    return {
        "job_id":            "job1",
        "processing_path":   "processing/job1/test.wav",
        "stage_plan":        STAGE_PLAN,
        "retry_attempt":     str(retry),
        "resume_from_stage": resume_from,
    }


def test_worker_runs_all_stages_from_start(tmp_path):
    from pipeline.worker import _process_job
    minio = MagicMock()
    minio.download_processing_file = MagicMock()
    minio.upload_job_outputs = MagicMock()

    stage_results = {"preprocess": tmp_path / "test_clean.wav",
                     "transcribe": tmp_path / "test.srt",
                     "summarize":  (tmp_path / "test_summary.md", tmp_path / "test_summary.json")}

    http_calls = []

    with patch("pipeline.worker._run_stage", side_effect=lambda stage, ctx, cfg, ws, prov: (
        {**ctx, "audio_path": stage_results["preprocess"]} if stage == "preprocess"
        else {**ctx, "srt_path": stage_results["transcribe"]} if stage == "transcribe"
        else {**ctx, "summary_md": stage_results["summarize"][0]}
    )) as mock_run, \
    patch("pipeline.worker._post_callback", side_effect=lambda url, body: http_calls.append(body)), \
    patch("pipeline.worker.runner._build_providers_for_stage", return_value={}):
        msg = make_mq_message()
        _process_job(msg, minio=minio, workspace=tmp_path, dagservice_url="http://localhost:8080")

    assert mock_run.call_count == 3
    assert len(http_calls) == 3
    assert all(c["status"] == "success" for c in http_calls)
    assert minio.upload_job_outputs.called


def test_worker_resumes_from_stage(tmp_path):
    from pipeline.worker import _process_job
    minio = MagicMock()
    minio.download_processing_file = MagicMock()
    minio.upload_job_outputs = MagicMock()
    ran_stages = []

    with patch("pipeline.worker._run_stage", side_effect=lambda stage, ctx, cfg, ws, prov: (
        ran_stages.append(stage) or ctx
    )), patch("pipeline.worker._post_callback"), \
    patch("pipeline.worker.runner._build_providers_for_stage", return_value={}):
        msg = make_mq_message(resume_from="transcribe")
        _process_job(msg, minio=minio, workspace=tmp_path, dagservice_url="http://localhost:8080")

    assert "preprocess" not in ran_stages  # skipped
    assert "transcribe" in ran_stages
    assert "summarize" in ran_stages


def test_worker_reports_failure_on_stage_error(tmp_path):
    from pipeline.worker import _process_job
    minio = MagicMock()
    minio.download_processing_file = MagicMock()
    http_calls = []

    with patch("pipeline.worker._run_stage", side_effect=RuntimeError("ffmpeg error")), \
         patch("pipeline.worker._post_callback", side_effect=lambda url, body: http_calls.append(body)), \
         patch("pipeline.worker.runner._build_providers_for_stage", return_value={}):
        msg = make_mq_message()
        _process_job(msg, minio=minio, workspace=tmp_path, dagservice_url="http://localhost:8080")

    assert http_calls[-1]["status"] == "failed"
    assert "ffmpeg error" in http_calls[-1]["error_msg"]
