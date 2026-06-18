# tests/events/test_mutations.py
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from saga.core.errors import NotFoundError
from saga.core.models import Event, EventCategory, EventType
from saga.events import mutations


class FakeStore:
    def __init__(self, events: list[Event]) -> None:
        self.events: dict[str, Event] = {e.event_id: e for e in events}

    async def get_event(self, event_id: str) -> Event | None:
        return self.events.get(event_id)

    async def delete_event(self, event_id: str) -> bool:
        return self.events.pop(event_id, None) is not None

    async def update_event(
        self,
        event_id: str,
        *,
        summary: str | None = None,
        occurred_at: datetime | None = None,
        confidence: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> Event | None:
        ev = self.events.get(event_id)
        if ev is None:
            return None
        data = ev.model_dump()
        updates: dict[str, Any] = {}
        if summary is not None:
            updates["summary"] = summary
        if occurred_at is not None:
            updates["occurred_at"] = occurred_at
        if confidence is not None:
            updates["confidence"] = confidence
        if details is not None:
            updates["details"] = details
        data.update(updates)
        self.events[event_id] = Event(**data)
        return self.events[event_id]

    async def merge_events(
        self, canonical_id: str, duplicate_ids: Sequence[str]
    ) -> Event | None:
        if canonical_id not in self.events:
            return None
        for d in duplicate_ids:
            if d != canonical_id:
                self.events.pop(d, None)
        return self.events[canonical_id]


class FakeSink:
    def __init__(self) -> None:
        self.appended: list[Event] = []

    async def append_event(self, event: Event) -> bool:
        self.appended.append(event)
        return True


def _ev(eid: str, doc: str = "doc1") -> Event:
    now = datetime.now(UTC)
    return Event(
        event_id=eid,
        category=EventCategory.CONTENT,
        event_type=EventType.APPOINTMENT,
        document_id=doc,
        occurred_at=now,
        recorded_at=now,
        actor="agent",
        summary="x",
    )


@pytest.mark.asyncio
async def test_delete_event_records_audit() -> None:
    store, sink = FakeStore([_ev("e1")]), FakeSink()
    await mutations.delete_event(store, sink, "e1")
    assert "e1" not in store.events
    assert sink.appended[0].event_type == EventType.EVENT_CURATED
    assert sink.appended[0].details["action"] == "delete"


@pytest.mark.asyncio
async def test_delete_unknown_raises() -> None:
    with pytest.raises(NotFoundError):
        await mutations.delete_event(FakeStore([]), FakeSink(), "nope")


@pytest.mark.asyncio
async def test_merge_event_records_audit() -> None:
    store, sink = FakeStore([_ev("c"), _ev("d1")]), FakeSink()
    out = await mutations.merge_events(store, sink, "c", ["d1"])
    assert out.event_id == "c" and "d1" not in store.events
    assert sink.appended[0].details["action"] == "merge"


@pytest.mark.asyncio
async def test_update_event_records_audit_and_returns_updated() -> None:
    store, sink = FakeStore([_ev("u1")]), FakeSink()
    updated = await mutations.update_event(store, sink, "u1", summary="new summary")
    assert updated.event_id == "u1"
    assert updated.summary == "new summary"
    assert sink.appended[0].event_type == EventType.EVENT_CURATED
    assert sink.appended[0].details["action"] == "update"
    assert sink.appended[0].details["event_id"] == "u1"


@pytest.mark.asyncio
async def test_update_unknown_raises() -> None:
    with pytest.raises(NotFoundError):
        await mutations.update_event(FakeStore([]), FakeSink(), "nope")


@pytest.mark.asyncio
async def test_best_effort_sink_failure_does_not_propagate() -> None:
    """A sink that raises must not propagate — the mutation must still succeed."""

    class FailingSink:
        async def append_event(self, event: Event) -> bool:
            raise RuntimeError("sink down")

    store = FakeStore([_ev("e2")])
    await mutations.delete_event(store, FailingSink(), "e2")
    assert "e2" not in store.events  # mutation succeeded despite sink failure
