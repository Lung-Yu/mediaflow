"""boto3 wrapper for MinIO operations used by the upload flow."""
import os
from pathlib import Path
from typing import Optional

# Guard prevents importlib.reload() (used in tests) from overwriting a mock
if "boto3" not in dir():
    import boto3  # noqa: F401
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
PUBLIC_ENDPOINT = os.getenv("MINIO_PUBLIC_ENDPOINT", ENDPOINT)
ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "mediaflow")
SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "changeme")
SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
INPUT_BUCKET = os.getenv("MINIO_INPUT_BUCKET", "mediaflow-input")
OUTPUT_BUCKET = os.getenv("MINIO_OUTPUT_BUCKET", "mediaflow-output")

_OUTPUT_STEMS = [".srt", "_summary.md", "_summary.json", "_chapters.json"]


class MinIOClient:
    def __init__(
        self, endpoint: str, access_key: str, secret_key: str,
        secure: bool, input_bucket: str, output_bucket: str,
        public_endpoint: str = "",
    ):
        scheme = "https" if secure else "http"
        # request_checksum_calculation="when_required" prevents boto3 1.35+ from
        # sending x-amz-checksum-* headers that MinIO returns NotImplemented for.
        self._s3 = boto3.client(  # boto3 is module-level; mock replaces it via patch
            "s3",
            endpoint_url=f"{scheme}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        self._internal_base = f"{scheme}://{endpoint}"
        self._public_base = f"{scheme}://{public_endpoint or endpoint}"
        self.input_bucket = input_bucket
        self.output_bucket = output_bucket

    def ensure_buckets(self) -> None:
        """Create buckets if absent; set CORS on input bucket for browser direct upload."""
        for bucket in [self.input_bucket, self.output_bucket]:
            try:
                self._s3.create_bucket(Bucket=bucket)
            except Exception:
                pass  # already exists
        self._s3.put_bucket_cors(
            Bucket=self.input_bucket,
            CORSConfiguration={
                "CORSRules": [{
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedOrigins": ["*"],
                    "ExposeHeaders": ["ETag"],
                }]
            },
        )

    def create_multipart_upload(self, key: str) -> str:
        """Initiate a multipart upload and return the upload ID."""
        resp = self._s3.create_multipart_upload(Bucket=self.input_bucket, Key=key)
        return resp["UploadId"]

    def _rewrite_url(self, url: str) -> str:
        """Replace internal Docker endpoint with public endpoint in presigned URLs."""
        if self._public_base != self._internal_base:
            url = url.replace(self._internal_base, self._public_base, 1)
        return url

    def presign_part_urls(self, key: str, upload_id: str, num_parts: int) -> list:
        """Return list of {part_number, url} for direct browser upload to MinIO."""
        return [
            {
                "part_number": i,
                "url": self._rewrite_url(self._s3.generate_presigned_url(
                    "upload_part",
                    Params={"Bucket": self.input_bucket, "Key": key,
                            "UploadId": upload_id, "PartNumber": i},
                    ExpiresIn=7200,
                )),
            }
            for i in range(1, num_parts + 1)
        ]

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list) -> None:
        """Finalise a multipart upload. parts: list of {part_number, etag}."""
        self._s3.complete_multipart_upload(
            Bucket=self.input_bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": p["part_number"], "ETag": p["etag"]} for p in parts
                ]
            },
        )

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """Abort an incomplete multipart upload."""
        self._s3.abort_multipart_upload(
            Bucket=self.input_bucket, Key=key, UploadId=upload_id
        )

    def download_to_file(self, key: str, dest: Path) -> None:
        """Download object from input bucket to local path. Blocking — use run_in_executor."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.input_bucket, key, str(dest))

    def upload_outputs(self, stem: str, output_dir: Path) -> None:
        """Upload SRT and summary files for stem to the output bucket."""
        for suffix in _OUTPUT_STEMS:
            path = output_dir / f"{stem}{suffix}"
            if path.exists():
                self._s3.upload_file(
                    Filename=str(path),
                    Bucket=self.output_bucket,
                    Key=f"{stem}/{stem}{suffix}",
                )

    def presign_get_url(self, bucket: str, key: str, expires_in: int = 604800) -> str:
        """Generate a presigned GET URL. Default expiry: 7 days."""
        url = self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return self._rewrite_url(url)


_client: Optional[MinIOClient] = None


def get_client() -> MinIOClient:
    assert _client is not None, "MinIO client not initialized — call init_client() first"
    return _client


def init_client() -> MinIOClient:
    """Create and cache the global MinIOClient from environment variables."""
    global _client
    _client = MinIOClient(
        endpoint=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=SECURE,
        input_bucket=INPUT_BUCKET,
        output_bucket=OUTPUT_BUCKET,
        public_endpoint=PUBLIC_ENDPOINT,
    )
    return _client
