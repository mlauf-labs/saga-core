"""Tests for the get_agenda MCP tool.

Mirrors the harness in test_mcp_get_timeline.py: builds the server via
``build_server(config, services)`` against a stub ``Services`` that exposes a
fake ``TimelineService``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from saga.api.dependencies import Services
from saga.core.config import AppConfig
from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery
from saga.mcp.server import build_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _structured(result: Any) -> Any:
    """Return the structured payload from a FastMCP call_tool result tuple."""
    assert isinstance(result, tuple), f"expected (content, structured) tuple, got {result!r}"
    return result[1]


def _make_event() -> Event:
    now = datetime.now(UTC)
    return Event(
        event_id=uuid.uuid4().hex,
        category=EventCategory.CONTENT,
        event_type=EventType.DOC_INGESTED,
        document_id=uuid.uuid4().hex,
        folder_id=None,
        occurred_at=now,
        recorded_at=now,
        actor="system",
        summary="Upcoming event.",
        details={},
    )


class _FakeTimelineService:
    """Minimal fake for TimelineService that returns a fixed event list."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self.last_query: EventQuery | None = None

    async def query(self, q: EventQuery) -> list[Event]:
        self.last_query = q
        return self._events


def _services_with_timeline(config: AppConfig, fake_timeline: _FakeTimelineService) -> Services:
    """Build a minimal Services stub with a fake timeline."""
    from saga.storage.postgres import PostgresStore
    from tests.conftest import (
        FakeQueue,
        InMemoryBinaryStore,
        InMemoryProjection,
    )

    db_mock = AsyncMock(spec=PostgresStore)

    svc = Services.__new__(Services)
    object.__setattr__(svc, "config", config)
    object.__setattr__(svc, "db", db_mock)
    object.__setattr__(svc, "opensearch", InMemoryProjection())
    object.__setattr__(svc, "minio", InMemoryBinaryStore())
    object.__setattr__(svc, "queue", FakeQueue())
    object.__setattr__(svc, "search", AsyncMock())
    object.__setattr__(svc, "timeline", fake_timeline)
    return svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.security.bearer_tokens = "test-token"
    return cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_agenda_tool_is_registered(config: AppConfig) -> None:
    """get_agenda must appear in the registered tool list."""
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)
    tools = await mcp.list_tools()
    assert "get_agenda" in {t.name for t in tools}


async def test_get_agenda_tool_has_description(config: AppConfig) -> None:
    """The tool description must be non-empty (loaded from prompts/mcp/get_agenda.md)."""
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)
    tools = await mcp.list_tools()
    agenda = next(t for t in tools if t.name == "get_agenda")
    assert agenda.description


async def test_get_agenda_returns_items(config: AppConfig) -> None:
    """Invoking get_agenda returns the serialized events from the fake service."""
    event = _make_event()
    fake = _FakeTimelineService([event])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)

    result = _structured(await mcp.call_tool("get_agenda", {}))
    assert "items" in result and "limit" in result and "offset" in result


async def test_get_agenda_forwards_agenda_query(config: AppConfig) -> None:
    """The tool hard-wires the agenda semantics into the EventQuery it issues."""
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)

    await mcp.call_tool("get_agenda", {"folder_id": "f1"})

    q = fake.last_query
    assert q is not None
    assert q.expand_recurrences is True
    assert q.descending is False
    assert q.order_by == "occurred_at"
    assert q.include_subtree is True
    assert q.categories == (EventCategory.CONTENT,)
    assert q.folder_id == "f1"


async def test_get_agenda_returns_empty_when_timeline_is_none(config: AppConfig) -> None:
    services = _services_with_timeline(config, _FakeTimelineService([]))
    object.__setattr__(services, "timeline", None)
    mcp = build_server(config, services)

    result = _structured(await mcp.call_tool("get_agenda", {}))
    assert result["items"] == []


async def test_get_agenda_clamps_limit_to_max_page_size(config: AppConfig) -> None:
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)

    await mcp.call_tool("get_agenda", {"limit": 99999})

    assert fake.last_query is not None
    assert fake.last_query.limit == config.timeline.max_page_size
