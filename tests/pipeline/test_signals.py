from __future__ import annotations

import json

import pytest

from saga.pipeline.signals import publish_signal


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


class BoomRedis:
    async def publish(self, channel: str, message: str) -> None:
        raise RuntimeError("down")


@pytest.mark.asyncio
async def test_publish_signal_emits_json() -> None:
    r = FakeRedis()
    await publish_signal(r, "saga:events", "document.ingested", document_id="doc1")
    assert len(r.published) == 1
    channel, message = r.published[0]
    assert channel == "saga:events"
    payload = json.loads(message)
    assert payload["topic"] == "document.ingested"
    assert payload["document_id"] == "doc1"
    assert "ts" in payload


@pytest.mark.asyncio
async def test_publish_signal_swallows_errors() -> None:
    await publish_signal(BoomRedis(), "saga:events", "x")  # must not raise


@pytest.mark.asyncio
async def test_publish_signal_extra_fields_are_included() -> None:
    r = FakeRedis()
    await publish_signal(r, "ch", "evt", foo="bar", num=42)
    payload = json.loads(r.published[0][1])
    assert payload["foo"] == "bar"
    assert payload["num"] == 42
    assert payload["topic"] == "evt"
