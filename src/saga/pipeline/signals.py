"""Best-effort Redis pub/sub announcements for external trigger consumers (saga-agents)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from saga.core.logging import get_logger

_log = get_logger("saga.pipeline.signals")


async def publish_signal(redis: Any, channel: str, topic: str, **fields: Any) -> None:
    """Publish a JSON signal to a Redis channel.

    Best-effort: any exception is logged and swallowed so that a signalling
    failure can never break or abort the ingestion pipeline.
    """
    payload = {"topic": topic, "ts": datetime.now(UTC).isoformat(), **fields}
    try:
        await redis.publish(channel, json.dumps(payload))
    except Exception as exc:  # best-effort; signalling must never break ingestion
        _log.warning("signal_publish_failed", topic=topic, error=str(exc))
