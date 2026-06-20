"""Compute an on-demand archive snapshot from all backends (best-effort)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from saga.core.logging import get_logger
from saga.core.models import ArchiveCounts
from saga.metrics.redis_probe import redis_stats

if TYPE_CHECKING:
    from saga.storage import MinioStore, OpenSearchStore, PostgresStore

_log = get_logger("saga.metrics.snapshot")

#: Substring identifying the chunk/vector index among OpenSearch indices.
_CHUNK_INDEX_HINT = "chunk"


class StorageSnapshot(BaseModel):
    postgres_bytes: int | None = None
    opensearch_bytes: int | None = None
    opensearch_docs: int | None = None
    minio_bytes: int | None = None
    minio_objects: int | None = None
    redis_bytes: int | None = None
    queue_depth: int | None = None


class Snapshot(BaseModel):
    counts: ArchiveCounts = Field(default_factory=ArchiveCounts)
    storage: StorageSnapshot = Field(default_factory=StorageSnapshot)
    chunks_total: int | None = None


class SnapshotService:
    """Assembles a :class:`Snapshot` from Postgres, OpenSearch, MinIO and Redis."""

    def __init__(
        self,
        db: PostgresStore,
        opensearch: OpenSearchStore,
        minio: MinioStore,
        redis: Any,  # noqa: ANN401 - arq ArqRedis
    ) -> None:
        self._db = db
        self._opensearch = opensearch
        self._minio = minio
        self._redis = redis

    async def collect(self) -> Snapshot:
        counts = await self._safe(self._db.aggregate_counts(), "postgres_counts", ArchiveCounts())
        storage = StorageSnapshot()
        storage.postgres_bytes = await self._safe(
            self._db.database_size_bytes(), "postgres_size", None
        )

        index_stats = await self._safe(self._opensearch.index_stats(), "opensearch", None)
        chunks_total: int | None = None
        if index_stats is not None:
            storage.opensearch_bytes = sum(v["size_bytes"] for v in index_stats.values())
            storage.opensearch_docs = sum(v["docs"] for v in index_stats.values())
            for name, v in index_stats.items():
                if _CHUNK_INDEX_HINT in name:
                    chunks_total = v["docs"]

        bucket = await self._safe(self._minio.bucket_stats(), "minio", None)
        if bucket is not None:
            storage.minio_bytes = bucket["size_bytes"]
            storage.minio_objects = bucket["objects"]

        redis_info = await self._safe(redis_stats(self._redis), "redis", None)
        if redis_info is not None:
            storage.redis_bytes = redis_info["used_memory_bytes"]
            storage.queue_depth = redis_info["queue_depth"]

        return Snapshot(counts=counts, storage=storage, chunks_total=chunks_total)

    async def _safe(self, coro: Any, source: str, default: Any) -> Any:  # noqa: ANN401
        try:
            return await coro
        except Exception as exc:  # best-effort: one bad source must not fail the snapshot
            _log.warning("snapshot_source_failed", source=source, error=str(exc))
            return default
