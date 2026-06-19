"""Synchronous MinIO helpers for the pipeline worker."""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.config import Config

_OUTPUT_SUFFIXES = [".srt", "_summary.md", "_summary.json", "_chapters.json", "_clean.wav"]


class PipelineMinIOClient:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        processing_bucket: str,
        output_bucket: str,
    ):
        scheme = "https" if secure else "http"
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )
        self.processing_bucket = processing_bucket
        self.output_bucket = output_bucket

    def download_processing_file(self, processing_key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.processing_bucket, processing_key, str(dest))

    def upload_output(self, job_id: str, local_path: Path, key_suffix: str) -> None:
        self._s3.upload_file(
            Filename=str(local_path),
            Bucket=self.output_bucket,
            Key=f"output/{job_id}/{key_suffix}",
        )

    def upload_job_outputs(self, job_id: str, stem: str, output_dir: Path) -> None:
        for suffix in _OUTPUT_SUFFIXES:
            path = output_dir / f"{stem}{suffix}"
            if path.exists():
                self.upload_output(job_id, path, key_suffix=f"{stem}{suffix}")


def client_from_env() -> PipelineMinIOClient:
    return PipelineMinIOClient(
        endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "mediaflow"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "changeme"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        processing_bucket=os.getenv("MINIO_PROCESSING_BUCKET", "mediaflow-processing"),
        output_bucket=os.getenv("MINIO_OUTPUT_BUCKET", "mediaflow-output"),
    )
