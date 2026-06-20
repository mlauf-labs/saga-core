from __future__ import annotations

from unittest.mock import AsyncMock

from saga.core.models import ArchiveCounts
from saga.metrics.snapshot import SnapshotService


def _db() -> AsyncMock:
    db = AsyncMock()
    db.aggregate_counts = AsyncMock(return_value=ArchiveCounts(documents_total=3))
    db.database_size_bytes = AsyncMock(return_value=4096)
    return db


async def test_collect_merges_sources() -> None:
    db = _db()
    opensearch = AsyncMock()
    opensearch.index_stats = AsyncMock(
        return_value={
            "saga_documents": {"docs": 3, "size_bytes": 100},
            "saga_document_chunks": {"docs": 12, "size_bytes": 200},
        }
    )
    minio = AsyncMock()
    minio.bucket_stats = AsyncMock(return_value={"objects": 3, "size_bytes": 500})
    redis = AsyncMock()
    redis.info = AsyncMock(return_value={"used_memory": 64})
    redis.zcard = AsyncMock(return_value=1)

    snap = await SnapshotService(db, opensearch, minio, redis).collect()
    assert snap.counts.documents_total == 3
    assert snap.storage.postgres_bytes == 4096
    assert snap.storage.opensearch_bytes == 300
    assert snap.storage.minio_bytes == 500
    assert snap.storage.redis_bytes == 64
    assert snap.storage.queue_depth == 1
    assert snap.chunks_total == 12


async def test_collect_degrades_when_a_source_fails() -> None:
    db = _db()
    opensearch = AsyncMock()
    opensearch.index_stats = AsyncMock(side_effect=RuntimeError("down"))
    minio = AsyncMock()
    minio.bucket_stats = AsyncMock(side_effect=RuntimeError("down"))
    redis = AsyncMock()
    redis.info = AsyncMock(side_effect=RuntimeError("down"))
    redis.zcard = AsyncMock(side_effect=RuntimeError("down"))

    snap = await SnapshotService(db, opensearch, minio, redis).collect()
    assert snap.counts.documents_total == 3  # postgres still works
    assert snap.storage.opensearch_bytes is None
    assert snap.storage.minio_bytes is None
    assert snap.storage.redis_bytes is None
