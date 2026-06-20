from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


def make_client():
    from pipeline.minio_io import PipelineMinIOClient
    return PipelineMinIOClient(
        endpoint="localhost:9000",
        access_key="mediaflow",
        secret_key="changeme",
        secure=False,
        processing_bucket="mediaflow-processing",
        output_bucket="mediaflow-output",
    )


def test_download_processing_file(tmp_path):
    client = make_client()
    dest = tmp_path / "audio.wav"
    with patch.object(client._s3, "download_file") as mock_dl:
        client.download_processing_file("processing/job1/audio.wav", dest)
    mock_dl.assert_called_once_with(
        "mediaflow-processing", "processing/job1/audio.wav", str(dest)
    )
    assert dest.parent.exists()


def test_upload_output_file(tmp_path):
    client = make_client()
    f = tmp_path / "test.srt"
    f.write_text("srt content")
    with patch.object(client._s3, "upload_file") as mock_ul:
        client.upload_output("job1", f, key_suffix="test.srt")
    mock_ul.assert_called_once_with(
        Filename=str(f),
        Bucket="mediaflow-output",
        Key="output/job1/test.srt",
    )


def test_upload_job_outputs_uploads_all_existing_files(tmp_path):
    client = make_client()
    (tmp_path / "stem.srt").write_text("srt")
    (tmp_path / "stem_summary.md").write_text("md")
    (tmp_path / "stem_summary.json").write_text("{}")
    (tmp_path / "stem_clean.wav").write_bytes(b"wav")
    # stem_chapters.json does NOT exist — should be skipped without error
    with patch.object(client._s3, "upload_file") as mock_ul:
        client.upload_job_outputs("job1", "stem", tmp_path)
    assert mock_ul.call_count == 4  # srt, md, json, wav (not chapters.json)
