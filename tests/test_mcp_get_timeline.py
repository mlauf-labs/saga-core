"""Tests for the get_timeline MCP tool (FR-timeline-mcp).

Mirrors the harness in test_mcp_server.py: builds the server via
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
        category=EventCategory.AUDIT,
        event_type=EventType.DOC_INGESTED,
        document_id=uuid.uuid4().hex,
        folder_id=None,
        occurred_at=now,
        recorded_at=now,
        actor="system",
        summary="Document ingested.",
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

    # We need a real db for Services but won't use it for timeline tests.
    # Use a no-op sentinel — the server only calls services.timeline for get_timeline.
    # However Services expects concrete types; use AsyncMock for db to avoid SQLite setup.
    db_mock = AsyncMock(spec=PostgresStore)

    svc = Services.__new__(Services)
    object.__setattr__(svc, "config", config)
    object.__setattr__(svc, "db", db_mock)
    object.__setattr__(svc, "opensearch", InMemoryProjection())
    object.__setattr__(svc, "minio", InMemoryBinaryStore())
    object.__setattr__(svc, "queue", FakeQueue())
    object.__setattr__(svc, "search", AsyncMock())
    object.__setattr__(svc, "timeline", fake_timeline)
    return svc  # type: ignore[return-value]


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


async def test_get_timeline_tool_is_registered(config: AppConfig) -> None:
    """get_timeline must appear in the registered tool list."""
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "get_timeline" in names


async def test_get_timeline_tool_has_description(config: AppConfig) -> None:
    """The tool description must be non-empty (loaded from prompts/mcp/get_timeline.md)."""
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)
    tools = await mcp.list_tools()
    tl_tool = next(t for t in tools if t.name == "get_timeline")
    assert tl_tool.description


async def test_get_timeline_returns_events(config: AppConfig) -> None:
    """Invoking get_timeline returns the serialized events from the fake service."""
    event = _make_event()
    fake = _FakeTimelineService([event])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)

    result = _structured(await mcp.call_tool("get_timeline", {}))
    assert "items" in result
    assert len(result["items"]) == 1
    assert result["items"][0]["event_id"] == event.event_id
    assert result["items"][0]["category"] == EventCategory.AUDIT.value


async def test_get_timeline_passes_filters_to_service(config: AppConfig) -> None:
    """Parameters are forwarded correctly to the EventQuery."""
    event = _make_event()
    fake = _FakeTimelineService([event])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)

    doc_id = uuid.uuid4().hex
    await mcp.call_tool(
        "get_timeline",
        {
            "document_id": doc_id,
            "category": "audit",
            "order_by": "occurred_at",
            "limit": 10,
            "offset": 5,
        },
    )

    assert fake.last_query is not None
    assert fake.last_query.document_id == doc_id
    assert fake.last_query.categories == (EventCategory.AUDIT,)
    assert fake.last_query.order_by == "occurred_at"
    assert fake.last_query.limit == 10
    assert fake.last_query.offset == 5


async def test_get_timeline_returns_empty_when_timeline_is_none(config: AppConfig) -> None:
    """When services.timeline is None, the tool returns an empty items list."""
    from unittest.mock import AsyncMock

    from saga.storage.postgres import PostgresStore
    from tests.conftest import FakeQueue, InMemoryBinaryStore, InMemoryProjection

    db_mock = AsyncMock(spec=PostgresStore)
    svc = Services.__new__(Services)
    object.__setattr__(svc, "config", config)
    object.__setattr__(svc, "db", db_mock)
    object.__setattr__(svc, "opensearch", InMemoryProjection())
    object.__setattr__(svc, "minio", InMemoryBinaryStore())
    object.__setattr__(svc, "queue", FakeQueue())
    object.__setattr__(svc, "search", AsyncMock())
    object.__setattr__(svc, "timeline", None)

    mcp = build_server(config, svc)  # type: ignore[arg-type]
    result = _structured(await mcp.call_tool("get_timeline", {}))
    assert result["items"] == []


async def test_get_timeline_clamps_limit_to_max_page_size(config: AppConfig) -> None:
    """limit is clamped to config.timeline.max_page_size."""
    fake = _FakeTimelineService([])
    services = _services_with_timeline(config, fake)
    mcp = build_server(config, services)

    await mcp.call_tool("get_timeline", {"limit": 999999})

    assert fake.last_query is not None
    assert fake.last_query.limit <= config.timeline.max_page_size
