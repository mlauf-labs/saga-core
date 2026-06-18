# tests/test_postgres_document_metadata.py
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Document
from saga.storage.postgres import PostgresStore


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    s = PostgresStore(config=None, engine=create_async_engine("sqlite+aiosqlite://"))  # type: ignore[arg-type]
    await s.bootstrap()
    return s


def _doc(**kw: object) -> Document:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base: dict[str, object] = {
        "document_id": "d1",
        "title": "t",
        "filename": "t.txt",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "content_hash": "h",
        "minio_object": "o",
        "created_at": now,
        "updated_at": now,
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


async def test_create_and_get_persists_metadata(store: PostgresStore) -> None:
    await store.create_document(_doc(metadata={"project": "Apollo"}))
    loaded = await store.get_document("d1")
    assert loaded is not None and loaded.metadata == {"project": "Apollo"}


async def test_update_replaces_metadata(store: PostgresStore) -> None:
    await store.create_document(_doc(metadata={"a": "1"}))
    updated = await store.update_document("d1", metadata={"b": "2"})
    assert updated.metadata == {"b": "2"}


async def test_update_leaves_metadata_unchanged_when_omitted(store: PostgresStore) -> None:
    await store.create_document(_doc(metadata={"a": "1"}))
    updated = await store.update_document("d1", title="new title")
    assert updated.metadata == {"a": "1"}
