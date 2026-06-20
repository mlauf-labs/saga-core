from __future__ import annotations

from unittest.mock import AsyncMock

from saga.metrics.redis_probe import redis_stats


async def test_redis_stats() -> None:
    redis = AsyncMock()
    redis.info = AsyncMock(return_value={"used_memory": 2048})
    redis.zcard = AsyncMock(return_value=7)
    stats = await redis_stats(redis)
    assert stats == {"used_memory_bytes": 2048, "queue_depth": 7}
