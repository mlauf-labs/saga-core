"""ARQ queue wiring (Redis) shared by the API (enqueue) and the worker (NFR-11)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from arq import create_pool
from arq.connections import RedisSettings

if TYPE_CHECKING:
    from arq.connections import ArqRedis

    from docstore.core.config import RedisConfig

#: Name of the ingestion job (must match the worker function name).
INGEST_JOB = "ingest_document"


def redis_settings(config: RedisConfig) -> RedisSettings:
    """Build ARQ ``RedisSettings`` from the configured Redis DSN."""
    return RedisSettings.from_dsn(config.url)


async def create_redis_pool(config: RedisConfig) -> ArqRedis:
    """Create an ARQ Redis pool for enqueueing jobs."""
    return await create_pool(redis_settings(config))
