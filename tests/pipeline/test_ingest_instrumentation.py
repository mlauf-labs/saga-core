"""End-to-end test: ingest_document wires metrics into Redis via instrumented_stage.

Runs a full ingest against fake infrastructure (SQLite db, in-memory fakes for
MinIO / OpenSearch / converters / analyzer / embedder / chunker) and a real
``fakeredis.aioredis.FakeRedis`` instance, then asserts that the metrics
aggregates were written.

The ctx fakes are adapted from tests/test_pipeline_stages.py which covers the
individual stages in isolation; here we wire them all through ``ingest_document``.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from saga.core.config import AppConfig
from saga.core.models import Document, DocumentStatus
from saga.llm.config import LlmConfig
from saga.llm.schemas import (
    DocTypeAssignment,
    FolderPlacement,
    Summary,
    TimelineExtraction,
    ValueExtraction,
)
from saga.metrics.redis_aggregate import read_pipeline_aggregates
from saga.pipeline.tasks import ingest_document
from saga.storage.postgres import PostgresStore

# ---------------------------------------------------------------------------
# Inline fakes (adapted from tests/test_pipeline_stages.py)
# ---------------------------------------------------------------------------


class _FakeConverter:
    name = "fake"

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        return "# Converted markdown"


class _FakeConverterRegistry:
    def resolve(self, *, filename: str, mime_type: str) -> _FakeConverter:
        return _FakeConverter()


class _FakeMinio:
    async def get_object(self, object_name: str) -> bytes:
        return b"%PDF-binary"


class _FakeOpenSearch:
    def __init__(self) -> None:
        self.projected: dict[str, Any] = {}
        self.deleted_chunks: list[str] = []
        self.indexed_chunks: list[Any] = []

    async def project_document(
        self,
        document: Any,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        self.projected[document.document_id] = document

    async def delete_chunks(self, document_id: str) -> None:
        self.deleted_chunks.append(document_id)

    async def index_chunks(self, chunks: list[Any]) -> int:
        self.indexed_chunks.extend(chunks)
        return len(chunks)

    # compute_similarity uses these OpenSearch search methods; return empty results
    async def similar_by_summary(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def similar_by_text(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class _FakeAnalyzer:
    async def classify_doc_type(self, **kwargs: Any) -> DocTypeAssignment | None:
        return DocTypeAssignment(doc_type="invoice", is_new=True, description="a bill", emoji="📄")

    async def extract_values(self, **kwargs: Any) -> ValueExtraction | None:
        return ValueExtraction(values=[])

    async def extract_timeline(self, **kwargs: Any) -> TimelineExtraction | None:
        return TimelineExtraction(events=[])

    async def summarize(self, **kwargs: Any) -> Summary | None:
        return Summary(title="Invoice", summary="An invoice document.")

    async def place_in_folder(self, **kwargs: Any) -> FolderPlacement | None:
        return FolderPlacement()

    async def place_in_folder_agentic(self, **kwargs: Any) -> None:
        return None


class _FakeEmbedder:
    dimension = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _ in texts]

    async def aclose(self) -> None:
        pass


class _FakeChunker:
    def split(self, content: str) -> list[str]:
        return [content] if content.strip() else []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> AsyncIterator[PostgresStore]:
    """SQLite-backed PostgresStore, bootstrapped in-memory."""
    tmp = Path(tempfile.mkdtemp()) / "saga-instr-test.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}", poolclass=NullPool)
    store = PostgresStore(AppConfig().postgres, engine=engine)
    await store.bootstrap()
    try:
        yield store
    finally:
        await store.close()
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_pipeline_records_stage_and_ingest_aggregates(db: PostgresStore) -> None:
    """A full ingest run leaves stage + ingest aggregates in Redis.

    After ``ingest_document`` completes:
    - ``agg.stages["convert"].count == 1``  (stage was timed and recorded)
    - ``agg.ingest["success"] == 1``        (success path recorded)
    """
    # Seed a document so ingest_document can find it
    now = datetime(2026, 6, 1, tzinfo=UTC)
    doc_id = "instr-test-doc-001"
    await db.create_document(
        Document(
            document_id=doc_id,
            title="invoice.pdf",
            filename="invoice.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            content_hash="testhash001",
            minio_object=f"saga-originals/{doc_id}",
            status=DocumentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
    )

    redis = fakeredis.aioredis.FakeRedis()

    ctx: dict[str, Any] = {
        "config": AppConfig(),
        "llm_config": LlmConfig(),
        "db": db,
        "opensearch": _FakeOpenSearch(),
        "minio": _FakeMinio(),
        "converters": _FakeConverterRegistry(),
        "analyzer": _FakeAnalyzer(),
        "chunker": _FakeChunker(),
        "embedder": _FakeEmbedder(),
        "redis": redis,
    }

    await ingest_document(ctx, doc_id)

    agg = await read_pipeline_aggregates(redis)
    assert agg.stages["convert"].count == 1
    assert agg.ingest["success"] == 1
