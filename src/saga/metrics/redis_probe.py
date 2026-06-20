"""Best-effort Redis memory + ARQ queue-depth probe."""

from __future__ import annotations

from typing import Any

#: Default ARQ queue sorted-set key (arq default).
ARQ_QUEUE_KEY = "arq:queue"


async def redis_stats(redis: Any) -> dict[str, int]:  # noqa: ANN401 - arq ArqRedis
    """Return ``{"used_memory_bytes", "queue_depth"}`` for the Redis instance."""
    info = await redis.info("memory")
    depth = await redis.zcard(ARQ_QUEUE_KEY)
    return {
        "used_memory_bytes": int(info.get("used_memory", 0)),
        "queue_depth": int(depth or 0),
    }
