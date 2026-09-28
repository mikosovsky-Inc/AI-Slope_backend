from app.core.config import Settings
from app.integrations.storage.local import LocalStorageProvider
from app.integrations.storage.s3 import S3StorageProvider
from app.shared.storage import StorageProvider


def create_storage_provider(settings: Settings) -> StorageProvider:
    if settings.storage_backend == "s3":
        return S3StorageProvider(settings)
    return LocalStorageProvider(settings.storage_local_root, max_bytes=settings.storage_max_bytes)
