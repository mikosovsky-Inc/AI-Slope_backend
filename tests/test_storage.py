import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.stub import ANY, Stubber
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.storage.factory import create_storage_provider
from app.integrations.storage.local import LocalStorageProvider
from app.integrations.storage.s3 import S3StorageProvider
from app.shared.storage import StorageError


@pytest.fixture
def s3_settings(settings):
    return Settings(
        **(
            settings.model_dump()
            | {
                "storage_backend": "s3",
                "s3_access_key_id": "test-access",
                "s3_secret_access_key": "test-secret",
                "s3_endpoint_url": "http://internal:8333",
                "s3_public_endpoint_url": "http://localhost:18333",
            }
        ),
        _env_file=None,
    )


def test_local_roundtrip_and_replacement(tmp_path):
    storage = LocalStorageProvider(tmp_path)
    key = "videos/scene/image.png"
    result = storage.put(key, BytesIO(b"test-image"), content_type="image/png")
    assert result.sha256 == hashlib.sha256(b"test-image").hexdigest()
    assert result.size_bytes == 10
    assert Path(urlsplit(storage.get_url(key)).path).read_bytes() == b"test-image"
    storage.put(key, BytesIO(b"updated"), content_type="image/png")
    assert (tmp_path / key).read_bytes() == b"updated"
    storage.delete(key)
    storage.delete(key)
    with pytest.raises(StorageError):
        storage.get_url(key)


@pytest.mark.parametrize(
    "key", ["../escape", "/absolute", "a/../b", "a//b", "a/", "a\\b", "a/%2e", ".", ""]
)
def test_local_rejects_unsafe_keys(tmp_path, key):
    storage = LocalStorageProvider(tmp_path)
    for action in (
        lambda: storage.put(key, BytesIO(b"x"), content_type="image/png"),
        lambda: storage.get_url(key),
        lambda: storage.delete(key),
    ):
        with pytest.raises(ValueError):
            action()


def test_local_rejects_symlinks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    storage = LocalStorageProvider(root)
    with pytest.raises(ValueError):
        storage.put("link/image", BytesIO(b"x"), content_type="image/png")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("payload", [b"", b"x" * 11])
def test_local_invalid_write_preserves_previous_object_and_cleans_temp(tmp_path, payload):
    storage = LocalStorageProvider(tmp_path, max_bytes=10)
    storage.put("image", BytesIO(b"original"), content_type="image/png")
    with pytest.raises(ValueError):
        storage.put("image", BytesIO(payload), content_type="image/png")
    assert (tmp_path / "image").read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["image"]


def test_local_normalizes_io_error(tmp_path):
    root = tmp_path / "file"
    root.write_text("not a directory")
    with pytest.raises(StorageError, match="Local storage unavailable"):
        LocalStorageProvider(root).put("image", BytesIO(b"x"), content_type="image/png")


def test_s3_put_delete_and_public_signed_url(s3_settings):
    storage = S3StorageProvider(s3_settings)
    try:
        with Stubber(storage.client) as stub:
            stub.add_response(
                "put_object",
                {},
                {
                    "Bucket": "ai-slop",
                    "Key": "video/image",
                    "Body": ANY,
                    "ContentLength": 4,
                    "ContentType": "image/png",
                    "Metadata": {"sha256": hashlib.sha256(b"test").hexdigest()},
                },
            )
            stub.add_response("delete_object", {}, {"Bucket": "ai-slop", "Key": "video/image"})
            result = storage.put("video/image", BytesIO(b"test"), content_type="image/png")
            assert result.size_bytes == 4
            url = urlsplit(storage.get_url(result.key))
            assert url.netloc == "localhost:18333"
            assert url.path == "/ai-slop/video/image"
            assert parse_qs(url.query)["X-Amz-Expires"] == ["300"]
            assert "test-secret" not in url.query
            storage.delete(result.key)
            stub.assert_no_pending_responses()
    finally:
        storage.close()


def test_s3_error_is_safe(s3_settings):
    storage = S3StorageProvider(s3_settings)
    try:
        with Stubber(storage.client) as stub:
            stub.add_client_error("put_object", "AccessDenied", "secret provider details")
            with pytest.raises(StorageError) as error:
                storage.put("image", BytesIO(b"x"), content_type="image/png")
            assert str(error.value) == "S3 storage unavailable"
    finally:
        storage.close()


def test_s3_validates_before_network(s3_settings):
    storage = S3StorageProvider(s3_settings.model_copy(update={"storage_max_bytes": 2}))
    try:
        with Stubber(storage.client):
            for key, payload in [("../x", b"x"), ("image", b""), ("image", b"xxx")]:
                with pytest.raises(ValueError):
                    storage.put(key, BytesIO(payload), content_type="image/png")
    finally:
        storage.close()


def test_storage_factory_and_config(settings, tmp_path):
    local = create_storage_provider(settings.model_copy(update={"storage_local_root": tmp_path}))
    assert isinstance(local, LocalStorageProvider)
    for overrides in [
        {"storage_backend": "s3"},
        {"s3_endpoint_url": "http://user:secret@host"},
        {"storage_max_bytes": 0},
        {"s3_url_seconds": 86400},
    ]:
        with pytest.raises(ValidationError):
            Settings(**(settings.model_dump() | overrides), _env_file=None)


def test_private_local_download_verifies_digest(tmp_path):
    storage = LocalStorageProvider(tmp_path)
    stored = storage.put("private/input", BytesIO(b"private-data"), content_type="video/mp4")
    target = BytesIO()
    downloaded = storage.download(stored.key, target)
    assert downloaded.sha256 == stored.sha256
    assert downloaded.size_bytes == stored.size_bytes
    assert target.getvalue() == b"private-data"
    with pytest.raises(ValueError):
        storage.download("../escape", BytesIO())


def test_private_s3_download_closes_body_and_enforces_limit(s3_settings):
    from botocore.response import StreamingBody

    storage = S3StorageProvider(s3_settings)
    body = StreamingBody(BytesIO(b"private-data"), 12)
    try:
        with Stubber(storage.client) as stub:
            stub.add_response(
                "get_object",
                {"Body": body, "ContentType": "video/mp4"},
                {"Bucket": s3_settings.s3_bucket, "Key": "private/input"},
            )
            target = BytesIO()
            stored = storage.download("private/input", target)
            assert stored.size_bytes == 12
            assert target.getvalue() == b"private-data"
            assert body._raw_stream.closed
    finally:
        storage.close()
