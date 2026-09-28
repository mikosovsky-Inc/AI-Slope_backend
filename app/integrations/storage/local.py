import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.integrations.storage.streams import copy_object
from app.shared.storage import StorageError, StorageObjectMissing, StoredObject, validate_key


class LocalStorageProvider:
    """Private, application-owned directory; never mount it as public static files."""

    def __init__(self, root: Path, *, max_bytes: int = 100 * 1024 * 1024) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def _path(self, key: str) -> Path:
        path = self.root / validate_key(key)
        # Reject even in-root symlinks; the root must not be writable by other users.
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError("Storage symlinks are not supported")
        return path

    def put(self, key: str, source: BinaryIO, *, content_type: str) -> StoredObject:
        path = self._path(key)
        temporary: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
                temporary = target.name
                result = copy_object(
                    source, target, key=key, content_type=content_type, max_bytes=self.max_bytes
                )
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            return result
        except OSError:
            raise StorageError("Local storage unavailable") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def download(self, key: str, target: BinaryIO) -> StoredObject:
        path = self._path(key)
        try:
            with path.open("rb") as source:
                return copy_object(
                    source,
                    target,
                    key=key,
                    content_type="application/octet-stream",
                    max_bytes=self.max_bytes,
                )
        except FileNotFoundError:
            raise StorageObjectMissing("Object unavailable") from None
        except OSError:
            raise StorageError("Local storage unavailable") from None

    def get_url(self, key: str) -> str:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("Object unavailable")
        return path.as_uri()

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise StorageError("Local storage unavailable") from None

    def close(self) -> None:
        pass
