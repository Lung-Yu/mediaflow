"""Unit tests for MinIO client wrapper."""
import math
from unittest.mock import MagicMock, patch, call
from pathlib import Path
import pytest


@pytest.fixture
def mock_boto3():
    with patch("api.minio_client.boto3") as mock:
        mock_s3 = MagicMock()
        mock.client.return_value = mock_s3
        yield mock, mock_s3


@pytest.fixture
def client(mock_boto3):
    import importlib
    import api.minio_client as mod
    importlib.reload(mod)
    return mod.MinIOClient(
        endpoint="localhost:9000",
        access_key="testkey",
        secret_key="testsecret",
        secure=False,
        input_bucket="test-input",
        output_bucket="test-output",
    )


def test_ensure_buckets_creates_both(client, mock_boto3):
    _, s3 = mock_boto3
    s3.create_bucket.side_effect = [None, None]
    client.ensure_buckets()
    assert s3.create_bucket.call_count == 2
    calls = [c[1]["Bucket"] for c in s3.create_bucket.call_args_list]
    assert "test-input" in calls
    assert "test-output" in calls


def test_ensure_buckets_sets_cors(client, mock_boto3):
    _, s3 = mock_boto3
    client.ensure_buckets()
    s3.put_bucket_cors.assert_called_once()
    args = s3.put_bucket_cors.call_args[1]
    assert args["Bucket"] == "test-input"
    rules = args["CORSConfiguration"]["CORSRules"]
    assert any("ETag" in r.get("ExposeHeaders", []) for r in rules)


def test_ensure_buckets_ignores_already_owned(client, mock_boto3):
    _, s3 = mock_boto3
    s3.create_bucket.side_effect = Exception("BucketAlreadyOwnedByYou")
    client.ensure_buckets()  # should not raise


def test_create_multipart_upload_returns_upload_id(client, mock_boto3):
    _, s3 = mock_boto3
    s3.create_multipart_upload.return_value = {"UploadId": "uid-123"}
    result = client.create_multipart_upload("stem/file.mp4")
    assert result == "uid-123"
    s3.create_multipart_upload.assert_called_with(Bucket="test-input", Key="stem/file.mp4")


def test_presign_part_urls_returns_correct_count(client, mock_boto3):
    _, s3 = mock_boto3
    s3.generate_presigned_url.return_value = "http://minio/presigned"
    parts = client.presign_part_urls("stem/f.mp4", "uid-1", 3)
    assert len(parts) == 3
    assert parts[0]["part_number"] == 1
    assert parts[2]["part_number"] == 3
    assert all("url" in p for p in parts)


def test_complete_multipart_upload(client, mock_boto3):
    _, s3 = mock_boto3
    parts = [{"part_number": 1, "etag": '"abc"'}, {"part_number": 2, "etag": '"def"'}]
    client.complete_multipart_upload("stem/f.mp4", "uid-1", parts)
    s3.complete_multipart_upload.assert_called_once()
    call_kwargs = s3.complete_multipart_upload.call_args[1]
    assert call_kwargs["Bucket"] == "test-input"
    assert call_kwargs["Key"] == "stem/f.mp4"
    assert call_kwargs["UploadId"] == "uid-1"
    assert len(call_kwargs["MultipartUpload"]["Parts"]) == 2


def test_abort_multipart_upload(client, mock_boto3):
    _, s3 = mock_boto3
    client.abort_multipart_upload("stem/f.mp4", "uid-1")
    s3.abort_multipart_upload.assert_called_with(
        Bucket="test-input", Key="stem/f.mp4", UploadId="uid-1"
    )


def test_download_to_file(client, mock_boto3, tmp_path):
    _, s3 = mock_boto3
    dest = tmp_path / "out.mp4"
    client.download_to_file("stem/f.mp4", dest)
    s3.download_file.assert_called_with("test-input", "stem/f.mp4", str(dest))


def test_upload_outputs_uploads_existing_files(client, mock_boto3, tmp_path):
    _, s3 = mock_boto3
    (tmp_path / "stem.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    (tmp_path / "stem_summary.md").write_text("# Summary")
    client.upload_outputs("stem", tmp_path)
    uploaded_keys = [c[1]["Key"] for c in s3.upload_file.call_args_list]
    assert "stem/stem.srt" in uploaded_keys
    assert "stem/stem_summary.md" in uploaded_keys


def test_upload_outputs_skips_missing_files(client, mock_boto3, tmp_path):
    _, s3 = mock_boto3
    client.upload_outputs("stem", tmp_path)  # no files exist
    s3.upload_file.assert_not_called()


def test_presign_get_url(client, mock_boto3):
    _, s3 = mock_boto3
    s3.generate_presigned_url.return_value = "http://minio/download"
    url = client.presign_get_url("test-output", "stem/stem.srt")
    assert url == "http://minio/download"
    s3.generate_presigned_url.assert_called_with(
        "get_object",
        Params={"Bucket": "test-output", "Key": "stem/stem.srt"},
        ExpiresIn=604800,
    )
