from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Event, EventCategory, EventType
from saga.storage.postgres import PostgresStore


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    yield s
    await s.close()


async def test_events_table_is_created(store):
    async with store.engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert "events" in tables


def _audit(dedupe_key: str | None) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id="",  # store assigns one
        category=EventCategory.AUDIT,
        event_type=EventType.PLACEMENT,
        document_id="d1",
        folder_id="f1",
        occurred_at=now,
        recorded_at=now,
        actor="pipeline",
        summary="Placed in Finance",
        dedupe_key=dedupe_key,
    )


async def _count_events(store) -> int:
    async with store.engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM events"))
        return int(result.scalar_one())


async def test_append_event_dedupes_on_key(store):
    assert await store.append_event(_audit("placement:d1:f1")) is True
    assert await store.append_event(_audit("placement:d1:f1")) is False  # duplicate no-op
    assert await _count_events(store) == 1


async def test_append_event_without_key_always_inserts(store):
    assert await store.append_event(_audit(None)) is True
    assert await store.append_event(_audit(None)) is True
    assert await _count_events(store) == 2
