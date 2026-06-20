from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from saga.storage.minio import MinioStore
from saga.storage.opensearch import OpenSearchStore


async def test_index_stats_parses_indices() -> None:
    client = MagicMock()
    client.indices.stats = AsyncMock(
        return_value={
            "indices": {
                "saga_documents": {
                    "primaries": {
                        "docs": {"count": 5},
                        "store": {"size_in_bytes": 100},
                    }
                },
            }
        }
    )
    store = OpenSearchStore(config=MagicMock(), client=client)
    stats = await store.index_stats()
    assert stats["saga_documents"] == {"docs": 5, "size_bytes": 100}


async def test_bucket_stats_sums_objects() -> None:
    client = MagicMock()
    client.list_objects.return_value = [MagicMock(size=10), MagicMock(size=20)]
    cfg = MagicMock()
    cfg.bucket = "b"
    store = MinioStore(config=cfg, client=client)
    stats = await store.bucket_stats()
    assert stats == {"objects": 2, "size_bytes": 30}
