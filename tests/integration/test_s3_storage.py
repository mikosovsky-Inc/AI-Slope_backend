import os
from io import BytesIO
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.integrations.storage.s3 import S3StorageProvider


def test_seaweedfs_roundtrip(settings):
    endpoint = os.getenv("TEST_S3_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("Set TEST_S3_ENDPOINT_URL to test SeaweedFS")
    config = Settings(
        **(
            settings.model_dump()
            | {
                "storage_backend": "s3",
                "s3_endpoint_url": endpoint,
                "s3_public_endpoint_url": endpoint,
                "s3_bucket": os.environ["TEST_S3_BUCKET"],
                "s3_access_key_id": os.environ["TEST_S3_ACCESS_KEY_ID"],
                "s3_secret_access_key": os.environ["TEST_S3_SECRET_ACCESS_KEY"],
            }
        ),
        _env_file=None,
    )
    storage = S3StorageProvider(config)
    key = f"integration/{uuid4()}/image.png"
    try:
        storage.put(key, BytesIO(b"test-image"), content_type="image/png")
        response = httpx.get(storage.get_url(key), timeout=10)
        assert response.status_code == 200
        assert response.content == b"test-image"
        assert response.headers["content-type"] == "image/png"
        # Bucket is private; unsigned requests do not reveal the object.
        assert httpx.get(f"{endpoint}/{config.s3_bucket}/{key}").status_code == 403
        storage.put(key, BytesIO(b"replacement"), content_type="image/png")
        assert httpx.get(storage.get_url(key)).content == b"replacement"
        storage.delete(key)
        storage.delete(key)
        assert httpx.get(storage.get_url(key)).status_code == 404
    finally:
        storage.delete(key)
        storage.close()
