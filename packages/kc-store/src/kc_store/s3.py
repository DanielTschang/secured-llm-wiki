"""BlobStore on one S3 bucket (per-space bucket, per-space MinIO user)."""

from typing import Any

from botocore.exceptions import ClientError

__all__ = ["S3Blobs"]


class S3Blobs:
    def __init__(self, client: Any, bucket: str) -> None:
        self._s3 = client
        self._bucket = bucket

    def put_if_absent(self, key: str, data: bytes) -> bool:
        try:
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, IfNoneMatch="*")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}:
                return False
            raise
        return True

    def get(self, key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise
        return resp["Body"].read()
