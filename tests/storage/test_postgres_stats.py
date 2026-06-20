from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Document, DocumentStatus
from saga.storage.postgres import PostgresStore


@pytest.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def _doc(
    store: PostgresStore,
    doc_id: str,
    *,
    size: int,
    mime: str,
    status: DocumentStatus,
) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    await store.create_document(
        Document(
            document_id=doc_id,
            title=doc_id,
            filename=f"{doc_id}.pdf",
            mime_type=mime,
            size_bytes=size,
            content_hash=doc_id,
            minio_object=f"b/{doc_id}",
            status=status,
            created_at=now,
            updated_at=now,
        )
    )


async def test_aggregate_counts(store: PostgresStore) -> None:
    await _doc(store, "a", size=100, mime="application/pdf", status=DocumentStatus.READY)
    await _doc(store, "b", size=300, mime="application/pdf", status=DocumentStatus.FAILED)
    counts = await store.aggregate_counts()
    assert counts.documents_total == 2
    assert counts.documents_by_status["ready"] == 1
    assert counts.documents_by_status["failed"] == 1
    assert counts.documents_by_mime["application/pdf"] == 2
    assert counts.documents_without_doc_type == 2
    assert counts.documents_without_folder == 2
    assert counts.size_bytes_sum == 400
    assert counts.size_bytes_max == 300
    assert counts.size_bytes_avg == 200.0
