"""Read path for the timeline/audit log (timeline design).

A single :class:`TimelineService` used by the REST routes, the MCP tool, and (later)
the OKF exporter. Folder filters are expanded to include the subtree here, so the
store stays a simple ``folder_id IN (...)`` query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from saga.storage.postgres import descendant_ids

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from saga.core.models import Event, EventCategory, EventType


class TimelineStore(Protocol):
    """The read operations the service depends on (satisfied by PostgresStore)."""

    async def parents_map(self) -> dict[str, str | None]: ...

    async def query_events(
        self,
        *,
        categories: Sequence[EventCategory] | None = ...,
        event_types: Sequence[EventType] | None = ...,
        document_id: str | None = ...,
        folder_ids: Sequence[str] | None = ...,
        occurred_from: datetime | None = ...,
        occurred_to: datetime | None = ...,
        order_by: Literal["recorded_at", "occurred_at"] = ...,
        descending: bool = ...,
        limit: int = ...,
        offset: int = ...,
    ) -> list[Event]: ...


@dataclass(frozen=True)
class EventQuery:
    """Filters for a timeline query (the 'which view' switch is ``categories``)."""

    categories: tuple[EventCategory, ...] | None = None
    event_types: tuple[EventType, ...] | None = None
    document_id: str | None = None
    folder_id: str | None = None
    include_subtree: bool = True
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    order_by: Literal["recorded_at", "occurred_at"] = "recorded_at"
    descending: bool = True
    limit: int = 100
    offset: int = 0


class TimelineService:
    """Resolves folder subtrees and delegates to the store's ``query_events``."""

    def __init__(self, store: TimelineStore) -> None:
        self._store = store

    async def query(self, q: EventQuery) -> list[Event]:
        folder_ids: list[str] | None = None
        if q.folder_id is not None:
            if q.include_subtree:
                parents = await self._store.parents_map()
                folder_ids = descendant_ids(q.folder_id, parents)
            else:
                folder_ids = [q.folder_id]
        return await self._store.query_events(
            categories=q.categories,
            event_types=q.event_types,
            document_id=q.document_id,
            folder_ids=folder_ids,
            occurred_from=q.occurred_from,
            occurred_to=q.occurred_to,
            order_by=q.order_by,
            descending=q.descending,
            limit=q.limit,
            offset=q.offset,
        )
