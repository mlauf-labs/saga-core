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


def _event(event_id: str, *, folder_id: str | None = None) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id=event_id,
        category=EventCategory.AUDIT,
        event_type=EventType.FOLDER_CREATED,
        folder_id=folder_id,
        recorded_at=now,
        actor="system",
        summary=f"event {event_id}",
    )


async def test_restore_events_inserts_verbatim_and_skips_existing(store: PostgresStore) -> None:
    await store.append_event(_event("e1"))

    restored = await store.restore_events([_event("e1"), _event("e2"), _event("e3")])

    assert restored == 2  # e1 already exists -> skipped
    all_events = await TimelineService(store).query(EventQuery(limit=100))
    assert {e.event_id for e in all_events} == {"e1", "e2", "e3"}


async def test_restore_events_empty_is_noop(store: PostgresStore) -> None:
    assert await store.restore_events([]) == 0
