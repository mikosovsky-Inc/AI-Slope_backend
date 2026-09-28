import tempfile
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.integrations.storage.streams import copy_object
from app.shared.storage import StorageError, StorageObjectMissing, StoredObject, validate_key


class S3StorageProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.s3_access_key_id or not settings.s3_secret_access_key:
            raise ValueError("S3 credentials are required")
        self.bucket = settings.s3_bucket
        self.max_bytes = settings.storage_max_bytes
        self.url_seconds = settings.s3_url_seconds
        options = dict(
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=settings.s3_timeout_seconds,
                read_timeout=settings.s3_timeout_seconds,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )
        self.client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **options)
        # Sign against the public endpoint; replacing the host after signing breaks SigV4.
        self.signer = boto3.client(
            "s3",
            endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url,
            **options,
        )

    def put(self, key: str, source: BinaryIO, *, content_type: str) -> StoredObject:
        validate_key(key)
        try:
            # Bounded disk-backed spool also supports non-seekable source streams and SDK retries.
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as payload:
                result = copy_object(
                    source, payload, key=key, content_type=content_type, max_bytes=self.max_bytes
                )
                payload.seek(0)
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=payload,
                    ContentLength=result.size_bytes,
                    ContentType=content_type,
                    Metadata={"sha256": result.sha256},
                )
                return result
        except (BotoCoreError, ClientError, OSError):
            raise StorageError("S3 storage unavailable") from None

    def download(self, key: str, target: BinaryIO) -> StoredObject:
        validate_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            source = response["Body"]
            try:
                return copy_object(
                    source,
                    target,
                    key=key,
                    content_type=response.get("ContentType", "application/octet-stream"),
                    max_bytes=self.max_bytes,
                )
            finally:
                source.close()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "NotFound", "404"):
                raise StorageObjectMissing("Object unavailable") from None
            raise StorageError("S3 storage unavailable") from None
        except (BotoCoreError, OSError):
            raise StorageError("S3 storage unavailable") from None

    def get_url(self, key: str) -> str:
        validate_key(key)
        try:
            return self.signer.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=self.url_seconds,
            )
        except (BotoCoreError, ClientError):
            raise StorageError("S3 storage unavailable") from None

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError):
            raise StorageError("S3 storage unavailable") from None

    def close(self) -> None:
        self.client.close()
        self.signer.close()
