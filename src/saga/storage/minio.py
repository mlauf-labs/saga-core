"""MinIO object storage for original binaries (FR-9).

The MinIO SDK is synchronous; calls are offloaded with ``asyncio.to_thread`` so the
async API/worker event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

from minio import Minio

from saga.core.errors import StorageError
from saga.core.logging import get_logger

if TYPE_CHECKING:
    from saga.core.config import MinioConfig

_log = get_logger("saga.storage.minio")


class MinioStore:
    """Stores and retrieves original document binaries."""

    def __init__(self, config: MinioConfig, client: Minio | None = None) -> None:
        self._config = config
        self._client = client

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                endpoint=self._config.endpoint,
                access_key=self._config.access_key,
                secret_key=self._config.secret_key,
                secure=self._config.secure,
            )
        return self._client

    @property
    def bucket(self) -> str:
        return self._config.bucket

    async def bootstrap(self) -> None:
        """Ensure the configured bucket exists."""
        try:
            exists = await asyncio.to_thread(self.client.bucket_exists, self.bucket)
            if not exists:
                await asyncio.to_thread(self.client.make_bucket, self.bucket)
                _log.info("bucket_created", bucket=self.bucket)
        except Exception as exc:
            raise StorageError(f"Failed to ensure MinIO bucket '{self.bucket}': {exc}") from exc

    async def put_object(self, object_name: str, data: bytes, content_type: str) -> str:
        """Store ``data`` under ``object_name``. Returns ``bucket/object_name``."""
        try:
            await asyncio.to_thread(
                self.client.put_object,
                self.bucket,
                object_name,
                io.BytesIO(data),
                len(data),
                content_type,
            )
        except Exception as exc:
            raise StorageError(f"Failed to store object '{object_name}' in MinIO: {exc}") from exc
        return f"{self.bucket}/{object_name}"

    async def get_object(self, object_name: str) -> bytes:
        """Read and return the bytes of ``object_name``."""

        def _read() -> bytes:
            response = None
            try:
                response = self.client.get_object(self.bucket, object_name)
                return bytes(response.read())
            finally:
                if response is not None:
                    response.close()
                    response.release_conn()

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:
            raise StorageError(f"Failed to read object '{object_name}' from MinIO: {exc}") from exc

    async def remove_object(self, object_name: str) -> None:
        """Delete ``object_name`` from the bucket."""
        try:
            await asyncio.to_thread(self.client.remove_object, self.bucket, object_name)
        except Exception as exc:
            raise StorageError(
                f"Failed to delete object '{object_name}' from MinIO: {exc}"
            ) from exc
