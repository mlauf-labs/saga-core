from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService
from saga.storage.postgres import PostgresStore


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def _seed_rule(store: PostgresStore) -> None:
    # Anchor in the PAST (2024) — the rule row's occurred_at precedes the agenda window,
    # but its yearly occurrences fall inside it. Expansion must still find them.
    await store.append_event(
        Event(
            event_id="rule-1",
            category=EventCategory.CONTENT,
            event_type=EventType.RECURRING,
            document_id="d1",
            occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
            recorded_at=datetime(2024, 5, 10, tzinfo=UTC),
            actor="llm",
            summary="Annual premium",
            details={"source_quote": "q", "recurrence": "FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1"},
        )
    )


async def test_expand_includes_occurrences_from_past_anchor(store: PostgresStore) -> None:
    await _seed_rule(store)
    service = TimelineService(store, recurrence_horizon_days=366, max_occurrences_per_rule=366)
    query = EventQuery(
        categories=(EventCategory.CONTENT,),
        occurred_from=datetime(2027, 1, 1, tzinfo=UTC),
        occurred_to=datetime(2027, 12, 31, tzinfo=UTC),
        order_by="occurred_at",
        descending=False,
        expand_recurrences=True,
        limit=100,
    )
    events = await service.query(query)
    # The bare rule (anchor 2024) is replaced by its 2027 occurrence.
    assert [e.event_id for e in events] == ["rule-1@2027-05-01"]
    assert events[0].details["occurrence_of"] == "rule-1"


async def test_expand_false_returns_bare_rule(store: PostgresStore) -> None:
    await _seed_rule(store)
    service = TimelineService(store)
    query = EventQuery(categories=(EventCategory.CONTENT,), limit=100)
    events = await service.query(query)
    assert [e.event_id for e in events] == ["rule-1"]  # unchanged default behaviour
