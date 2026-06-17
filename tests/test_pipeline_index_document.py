from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.errors import NotFoundError
from saga.core.models import Chunk, Document, DocumentStatus
from saga.pipeline.tasks import index_document
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeEmbedder


class _Chunker:
    def split(self, text: str) -> list[str]:
        return [block for block in text.split("\n\n") if block.strip()]


class _Projection:
    """Projection double implementing the three methods index_chunks calls."""

    def __init__(self) -> None:
        self.projected: dict[str, Document] = {}
        self.chunks: dict[str, int] = {}

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        self.projected[document.document_id] = document

    async def delete_chunks(self, document_id: str) -> None:
        self.chunks.pop(document_id, None)

    async def index_chunks(self, chunks: list[Chunk]) -> int:
        for chunk in chunks:
            self.chunks[chunk.document_id] = self.chunks.get(chunk.document_id, 0) + 1
        return len(chunks)


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def test_index_document_projects_and_chunks_without_llm(store: PostgresStore) -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    await store.create_document(
        Document(
            document_id="d1",
            title="Doc",
            filename="d.md",
            mime_type="text/markdown",
            size_bytes=20,
            content_hash="h",
            minio_object="saga-originals/d1",
            status=DocumentStatus.READY,
            summary="A summary.",
            content_markdown="Para one.\n\nPara two.",
            created_at=now,
            updated_at=now,
        )
    )
    projection = _Projection()
    ctx: dict[str, Any] = {
        "db": store,
        "opensearch": projection,
        "embedder": FakeEmbedder(),
        "chunker": _Chunker(),
    }

    await index_document(ctx, "d1")

    assert "d1" in projection.projected
    assert projection.chunks["d1"] == 2


async def test_index_document_raises_for_missing_document(store: PostgresStore) -> None:
    ctx: dict[str, Any] = {
        "db": store,
        "opensearch": _Projection(),
        "embedder": FakeEmbedder(),
        "chunker": _Chunker(),
    }
    with pytest.raises(NotFoundError):
        await index_document(ctx, "missing")
