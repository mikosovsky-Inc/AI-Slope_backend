import hashlib
from typing import BinaryIO

from app.shared.storage import StoredObject


def copy_object(
    source: BinaryIO, target: BinaryIO, *, key: str, content_type: str, max_bytes: int
) -> StoredObject:
    if not content_type or len(content_type) > 100 or any(ord(c) < 32 for c in content_type):
        raise ValueError("Invalid content type")
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(min(1024 * 1024, max_bytes - size + 1)):
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("Object exceeds storage size limit")
        digest.update(chunk)
        target.write(chunk)
    if size == 0:
        raise ValueError("Empty objects are not supported")
    return StoredObject(
        key=key, size_bytes=size, sha256=digest.hexdigest(), content_type=content_type
    )
