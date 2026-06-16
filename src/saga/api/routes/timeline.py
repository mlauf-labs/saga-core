"""Timeline endpoints: one read path over the tagged event store."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated

from fastapi import APIRouter, Query

from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import TimelineResponse
from saga.core.errors import SagaError
from saga.core.models import EventCategory, EventType  # noqa: TC001
from saga.events import EventQuery

router = APIRouter(tags=["timeline"], dependencies=[AuthDep])


def _build_query(
    services: ServicesDep,
    *,
    document_id: str | None,
    folder_id: str | None,
    include_subtree: bool,
    categories: list[EventCategory] | None,
    event_types: list[EventType] | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    order_by: str,
    limit: int,
    offset: int,
) -> EventQuery:
    cfg = services.config.timeline
    effective_limit = min(limit or cfg.default_page_size, cfg.max_page_size)
    return EventQuery(
        categories=tuple(categories) if categories else None,
        event_types=tuple(event_types) if event_types else None,
        document_id=document_id,
        folder_id=folder_id,
        include_subtree=include_subtree,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        order_by="occurred_at" if order_by == "occurred_at" else "recorded_at",
        limit=effective_limit,
        offset=offset,
    )


@router.get("/timeline", response_model=TimelineResponse, summary="Query the timeline/audit log")
async def get_timeline(
    services: ServicesDep,
    document_id: Annotated[str | None, Query()] = None,
    folder_id: Annotated[
        str | None, Query(description="Folder scope (subtree by default).")
    ] = None,
    include_subtree: Annotated[bool, Query()] = True,
    category: Annotated[list[EventCategory] | None, Query()] = None,
    event_type: Annotated[list[EventType] | None, Query()] = None,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
    order_by: Annotated[str, Query(pattern="^(recorded_at|occurred_at)$")] = "recorded_at",
    limit: Annotated[int, Query(ge=0)] = 0,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    query = _build_query(
        services,
        document_id=document_id,
        folder_id=folder_id,
        include_subtree=include_subtree,
        categories=category,
        event_types=event_type,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    events = await services.timeline.query(query)
    return TimelineResponse(items=events, limit=query.limit, offset=query.offset)


@router.get(
    "/documents/{document_id}/timeline",
    response_model=TimelineResponse,
    summary="Timeline of a single document",
)
async def get_document_timeline(
    services: ServicesDep,
    document_id: str,
    category: Annotated[list[EventCategory] | None, Query()] = None,
    order_by: Annotated[str, Query(pattern="^(recorded_at|occurred_at)$")] = "recorded_at",
    limit: Annotated[int, Query(ge=0)] = 0,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    query = _build_query(
        services,
        document_id=document_id,
        folder_id=None,
        include_subtree=False,
        categories=category,
        event_types=None,
        occurred_from=None,
        occurred_to=None,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    events = await services.timeline.query(query)
    return TimelineResponse(items=events, limit=query.limit, offset=query.offset)
