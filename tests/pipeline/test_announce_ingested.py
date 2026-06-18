"""Unit tests for _announce_ingested helper in saga.pipeline.tasks."""

from __future__ import annotations

import json

import pytest

from saga.core.config import AppConfig, EventsConfig
from saga.pipeline.tasks import _announce_ingested


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


@pytest.mark.asyncio
async def test_announce_ingested_publishes_when_enabled() -> None:
    redis = FakeRedis()
    config = AppConfig(events=EventsConfig(publish=True, channel="saga:events"))
    ctx: dict = {"redis": redis}
    await _announce_ingested(ctx, config, "doc-abc")
    assert len(redis.published) == 1
    channel, message = redis.published[0]
    assert channel == "saga:events"
    payload = json.loads(message)
    assert payload["topic"] == "document.ingested"
    assert payload["document_id"] == "doc-abc"
    assert "ts" in payload


@pytest.mark.asyncio
async def test_announce_ingested_does_nothing_when_publish_false() -> None:
    redis = FakeRedis()
    config = AppConfig(events=EventsConfig(publish=False))
    ctx: dict = {"redis": redis}
    await _announce_ingested(ctx, config, "doc-xyz")
    assert redis.published == []


@pytest.mark.asyncio
async def test_announce_ingested_does_nothing_when_redis_absent() -> None:
    config = AppConfig(events=EventsConfig(publish=True))
    ctx: dict = {}  # no redis key
    # Must not raise
    await _announce_ingested(ctx, config, "doc-nored")


@pytest.mark.asyncio
async def test_announce_ingested_does_nothing_when_redis_is_none() -> None:
    config = AppConfig(events=EventsConfig(publish=True))
    ctx: dict = {"redis": None}
    await _announce_ingested(ctx, config, "doc-none")
    # No error, no publish


@pytest.mark.asyncio
async def test_announce_ingested_swallows_exception_from_broken_config() -> None:
    """Outer try/except must catch errors raised before publish_signal is reached."""

    class BrokenConfig:
        @property
        def events(self) -> None:  # type: ignore[override]
            raise RuntimeError("config exploded")

    ctx: dict = {}
    # Must not raise even though accessing config.events raises
    await _announce_ingested(ctx, BrokenConfig(), "doc-broken")  # type: ignore[arg-type]
