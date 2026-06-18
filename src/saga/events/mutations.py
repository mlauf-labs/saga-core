"""Shared event-curation operations (delete/update/merge) used by MCP write tools.

Each mutation applies the change via the store and records a best-effort EVENT_CURATED
audit event so curation by background agents stays visible on the timeline.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from saga.core.errors import NotFoundError
from saga.core.logging import get_logger
from saga.core.models import Event, EventCategory, EventType

if TYPE_CHECKING:
    from collections.abc import Sequence

_log = get_logger("saga.events.mutations")


class EventMutationStore(Protocol):
    async def get_event(self, event_id: str) -> Event | None: ...
    async def delete_event(self, event_id: str) -> bool: ...
    async def update_event(self, event_id: str, *, summary: str | None = ...,
                           occurred_at: datetime | None = ..., confidence: float | None = ...,
                           details: dict[str, Any] | None = ...) -> Event | None: ...
    async def merge_events(self, canonical_id: str, duplicate_ids: Sequence[str]) -> Event | None: ...


class EventSink(Protocol):
    async def append_event(self, event: Event) -> bool: ...


async def _record(sink: EventSink, *, actor: str, summary: str,
                  document_id: str | None, details: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    event = Event(event_id=uuid.uuid4().hex, category=EventCategory.AUDIT,
                  event_type=EventType.EVENT_CURATED, document_id=document_id,
                  occurred_at=now, recorded_at=now, actor=actor, summary=summary, details=details)
    try:
        await sink.append_event(event)
    except Exception as exc:  # best-effort; never fail the mutation
        _log.warning("audit_event_append_failed", error=str(exc))


async def delete_event(store: EventMutationStore, sink: EventSink, event_id: str,
                       *, actor: str = "agent") -> None:
    existing = await store.get_event(event_id)
    if existing is None:
        raise NotFoundError(f"Event '{event_id}' was not found; nothing to delete.")
    await store.delete_event(event_id)
    await _record(sink, actor=actor, summary=f"Deleted event {event_id}",
                  document_id=existing.document_id, details={"action": "delete", "event_id": event_id})


async def update_event(store: EventMutationStore, sink: EventSink, event_id: str, *,
                       summary: str | None = None, occurred_at: datetime | None = None,
                       confidence: float | None = None, details: dict[str, Any] | None = None,
                       actor: str = "agent") -> Event:
    updated = await store.update_event(event_id, summary=summary, occurred_at=occurred_at,
                                       confidence=confidence, details=details)
    if updated is None:
        raise NotFoundError(f"Event '{event_id}' was not found; nothing to update.")
    await _record(sink, actor=actor, summary=f"Updated event {event_id}",
                  document_id=updated.document_id, details={"action": "update", "event_id": event_id})
    return updated


async def merge_events(store: EventMutationStore, sink: EventSink, canonical_id: str,
                       duplicate_ids: Sequence[str], *, actor: str = "agent") -> Event:
    merged = await store.merge_events(canonical_id, duplicate_ids)
    if merged is None:
        raise NotFoundError(f"Canonical event '{canonical_id}' was not found; cannot merge.")
    await _record(sink, actor=actor,
                  summary=f"Merged {len(list(duplicate_ids))} event(s) into {canonical_id}",
                  document_id=merged.document_id,
                  details={"action": "merge", "canonical_id": canonical_id,
                           "duplicate_ids": list(duplicate_ids)})
    return merged
