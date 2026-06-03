"""Storage adapters: OpenSearch (doc + vector indices) and MinIO."""

from __future__ import annotations

from docstore.storage.minio import MinioStore
from docstore.storage.opensearch import OpenSearchStore

__all__ = ["MinioStore", "OpenSearchStore"]
