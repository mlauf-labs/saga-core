"""Read path for the timeline/audit log (timeline design).

A single :class:`TimelineService` used by the REST routes, the MCP tool, and (later)
the OKF exporter. Folder filters are expanded to the subtree here, and document-level
events are matched via current folder membership (design §6.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from saga.core.models import Event, EventType
from saga.events.recurrence import expand_recurrences
from saga.storage.postgres import descendant_ids

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.models import EventCategory

_RULE_PAGE = 500


class TimelineStore(Protocol):
    """The read operations the service depends on (satisfied by PostgresStore)."""

    async def parents_map(self) -> dict[str, str | None]: ...

    async def document_ids_in_folders(self, folder_ids: Sequence[str]) -> list[str]: ...

    async def query_events(
        self,
        *,
        categories: Sequence[EventCategory] | None = ...,
        event_types: Sequence[EventType] | None = ...,
        document_id: str | None = ...,
        folder_ids: Sequence[str] | None = ...,
        document_ids: Sequence[str] | None = ...,
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
    expand_recurrences: bool = False


class TimelineService:
    """Resolves folder subtrees and delegates to the store's ``query_events``."""

    def __init__(
        self,
        store: TimelineStore,
        *,
        recurrence_horizon_days: int = 366,
        max_occurrences_per_rule: int = 366,
    ) -> None:
        self._store = store
        self._horizon_days = recurrence_horizon_days
        self._max_occurrences = max_occurrences_per_rule

    async def _resolve_scope(self, q: EventQuery) -> tuple[list[str] | None, list[str] | None]:
        if q.folder_id is None:
            return None, None
        if q.include_subtree:
            parents = await self._store.parents_map()
            folder_ids = descendant_ids(q.folder_id, parents)
        else:
            folder_ids = [q.folder_id]
        document_ids = await self._store.document_ids_in_folders(folder_ids)
        return folder_ids, document_ids

    async def query(self, q: EventQuery) -> list[Event]:
        folder_ids, document_ids = await self._resolve_scope(q)
        if q.expand_recurrences:
            return await self._query_expanded(q, folder_ids, document_ids)
        return await self._store.query_events(
            categories=q.categories,
            event_types=q.event_types,
            document_id=q.document_id,
            folder_ids=folder_ids,
            document_ids=document_ids,
            occurred_from=q.occurred_from,
            occurred_to=q.occurred_to,
            order_by=q.order_by,
            descending=q.descending,
            limit=q.limit,
            offset=q.offset,
        )

    async def _query_expanded(
        self,
        q: EventQuery,
        folder_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[Event]:
        now = datetime.now(UTC)
        window_start = q.occurred_from or now
        window_end = q.occurred_to or (now + timedelta(days=self._horizon_days))
        # Recurring rules: fetch ALL in scope (no lower time bound — a rule's anchor
        # often precedes the window while its occurrences fall inside it).
        rules = await self._fetch_all(
            categories=q.categories,
            event_types=(EventType.RECURRING,),
            document_id=q.document_id,
            folder_ids=folder_ids,
            document_ids=document_ids,
            occurred_from=None,
            occurred_to=None,
        )
        occurrences = expand_recurrences(
            rules,
            window_start=window_start,
            window_end=window_end,
            max_occurrences=self._max_occurrences,
        )
        # Non-recurring events within the window. The caller's ``event_types`` filter is
        # honoured here; any bare RECURRING rows are dropped below (the agenda comes from
        # the expanded occurrences, not the rule rows).
        others = await self._fetch_all(
            categories=q.categories,
            event_types=q.event_types,
            document_id=q.document_id,
            folder_ids=folder_ids,
            document_ids=document_ids,
            occurred_from=window_start,
            occurred_to=window_end,
        )
        others = [e for e in others if e.event_type != EventType.RECURRING]
        merged = sorted(
            [*others, *occurrences],
            key=lambda e: e.occurred_at or e.recorded_at,
            reverse=q.descending,
        )
        end = q.offset + q.limit if q.limit else None
        return merged[q.offset : end]

    async def _fetch_all(
        self,
        *,
        categories: tuple[EventCategory, ...] | None,
        event_types: tuple[EventType, ...] | None,
        document_id: str | None,
        folder_ids: list[str] | None,
        document_ids: list[str] | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> list[Event]:
        """Page query_events (occurred_at asc) until a short page; return everything."""
        out: list[Event] = []
        offset = 0
        while True:
            page = await self._store.query_events(
                categories=categories,
                event_types=event_types,
                document_id=document_id,
                folder_ids=folder_ids,
                document_ids=document_ids,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                order_by="occurred_at",
                descending=False,
                limit=_RULE_PAGE,
                offset=offset,
            )
            out.extend(page)
            if len(page) < _RULE_PAGE:
                return out
            offset += _RULE_PAGE
