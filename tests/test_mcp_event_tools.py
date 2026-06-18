"""Behavioural tests for the three event-mutation MCP tools (FR-23).

Follows the harness in test_mcp_server.py: builds the server via
``build_server(config, services)`` using the real SQLite-backed PostgresStore
provided by the ``services`` fixture from conftest.py.  Events are seeded
directly via ``services.db.append_event``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from saga.api.dependencies import Services
from saga.core.models import Event, EventCategory, EventType
from saga.mcp.server import build_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _structured(result: Any) -> Any:
    """Return the structured payload from a FastMCP call_tool result tuple."""
    assert isinstance(result, tuple), f"expected (content, structured) tuple, got {result!r}"
    return result[1]


def _make_event(*, event_id: str | None = None, document_id: str | None = None) -> Event:
    now = datetime.now(UTC)
    return Event(
        event_id=event_id or uuid.uuid4().hex,
        category=EventCategory.CONTENT,
        event_type=EventType.DOC_INGESTED,
        document_id=document_id,
        folder_id=None,
        occurred_at=now,
        recorded_at=now,
        actor="system",
        summary="Test event.",
        details={},
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_delete_event_existing_returns_deleted(services: Services) -> None:
    """delete_event on an existing event returns {status: deleted, event_id: ...}."""
    event = _make_event()
    await services.db.append_event(event)
    mcp = build_server(services.config, services)

    result = _structured(await mcp.call_tool("delete_event", {"event_id": event.event_id}))
    assert result == {"status": "deleted", "event_id": event.event_id}


async def test_delete_event_actually_removes_it(services: Services) -> None:
    """After delete_event, the event no longer exists in the store."""
    event = _make_event()
    await services.db.append_event(event)
    mcp = build_server(services.config, services)

    await mcp.call_tool("delete_event", {"event_id": event.event_id})

    fetched = await services.db.get_event(event.event_id)
    assert fetched is None


async def test_delete_event_unknown_returns_error(services: Services) -> None:
    """delete_event on a non-existent id returns {error: ...} and does not raise."""
    mcp = build_server(services.config, services)
    result = _structured(await mcp.call_tool("delete_event", {"event_id": "no-such-event"}))
    assert "error" in result
    assert "no-such-event" in result["error"]


async def test_merge_events_returns_canonical_dump(services: Services) -> None:
    """merge_events returns the model_dump of the canonical event."""
    canonical = _make_event()
    dup1 = _make_event()
    dup2 = _make_event()
    for ev in (canonical, dup1, dup2):
        await services.db.append_event(ev)

    mcp = build_server(services.config, services)
    result = _structured(
        await mcp.call_tool(
            "merge_events",
            {
                "canonical_event_id": canonical.event_id,
                "duplicate_event_ids": [dup1.event_id, dup2.event_id],
            },
        )
    )

    assert result["event_id"] == canonical.event_id
    # Duplicates must be recorded in the canonical event's details
    assert dup1.event_id in result["details"]["merged_from_event_ids"]
    assert dup2.event_id in result["details"]["merged_from_event_ids"]


async def test_merge_events_removes_duplicates(services: Services) -> None:
    """merge_events deletes the duplicate events from the store."""
    canonical = _make_event()
    dup = _make_event()
    for ev in (canonical, dup):
        await services.db.append_event(ev)

    mcp = build_server(services.config, services)
    await mcp.call_tool(
        "merge_events",
        {
            "canonical_event_id": canonical.event_id,
            "duplicate_event_ids": [dup.event_id],
        },
    )

    assert await services.db.get_event(dup.event_id) is None
    assert await services.db.get_event(canonical.event_id) is not None


async def test_merge_events_unknown_canonical_returns_error(services: Services) -> None:
    """merge_events on a non-existent canonical id returns {error: ...}."""
    mcp = build_server(services.config, services)
    result = _structured(
        await mcp.call_tool(
            "merge_events",
            {
                "canonical_event_id": "ghost",
                "duplicate_event_ids": [],
            },
        )
    )
    assert "error" in result


async def test_update_event_with_occurred_at_string(services: Services) -> None:
    """update_event parses the ISO string and persists the new occurred_at."""
    event = _make_event()
    await services.db.append_event(event)

    mcp = build_server(services.config, services)
    new_dt = "2025-01-15T12:00:00+00:00"
    result = _structured(
        await mcp.call_tool(
            "update_event",
            {"event_id": event.event_id, "occurred_at": new_dt},
        )
    )

    assert result["event_id"] == event.event_id
    # The returned occurred_at must reflect the update
    assert "2025-01-15" in result["occurred_at"]


async def test_update_event_unknown_returns_error(services: Services) -> None:
    """update_event on a non-existent id returns {error: ...}."""
    mcp = build_server(services.config, services)
    result = _structured(
        await mcp.call_tool("update_event", {"event_id": "no-such-event", "summary": "x"})
    )
    assert "error" in result
