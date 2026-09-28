import re
from typing import BinaryIO, Protocol

from pydantic import BaseModel, Field


class StorageError(Exception):
    """Safe boundary for filesystem/SDK errors; no provider details in the message."""


class StoredObject(BaseModel):
    key: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1, max_length=100)


def validate_key(key: str) -> str:
    # Canonical relative keys, shared by both adapters; no encoded paths or backslashes.
    if (
        not 1 <= len(key) <= 512
        or not re.fullmatch(r"[a-zA-Z0-9_./-]+", key)
        or any(part in ("", ".", "..") for part in key.split("/"))
    ):
        raise ValueError("Invalid storage key")
    return key


class StorageProvider(Protocol):
    def put(self, key: str, source: BinaryIO, *, content_type: str) -> StoredObject:
        """Read from the current stream position. Reusing a key replaces that object."""
        ...

    def get_url(self, key: str) -> str:
        """Internal file URI (local) or expiring bearer download URL (S3)."""
        ...

    def delete(self, key: str) -> None:
        """Idempotent: deleting an absent object succeeds."""
        ...

    def close(self) -> None: ...
