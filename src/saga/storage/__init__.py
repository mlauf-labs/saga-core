"""Storage adapters: Postgres (system of record), OpenSearch (search projection),
and MinIO (original binaries)."""

from __future__ import annotations

from saga.storage.minio import MinioStore
from saga.storage.opensearch import OpenSearchStore
from saga.storage.postgres import PostgresStore

__all__ = ["MinioStore", "OpenSearchStore", "PostgresStore"]
