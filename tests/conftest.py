"""Shared test fixtures.

API/service tests run against a *real* :class:`PostgresStore` backed by an in-memory
SQLite database (aiosqlite), so the relational system of record is exercised for real.
OpenSearch is replaced by a small in-memory projection, MinIO/Redis/embeddings by
fakes, and the search engine by a db-backed fake that implements the ``SearchEngine``
protocol (the real RRF :class:`SearchService` is unit-tested separately).
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from saga.api.app import create_app
from saga.api.dependencies import Services
from saga.core.config import AppConfig
from saga.core.errors import ValidationError
from saga.core.models import (
    Document,
    DocumentStatus,
    HybridSearchResult,
    SearchResultItem,
)
from saga.storage.opensearch import ProjectionRecord
from saga.storage.postgres import PostgresStore

if TYPE_CHECKING:
    from saga.core.models import FolderNode

TEST_TOKEN = "test-token"


# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #


class InMemoryBinaryStore:
    """In-memory stand-in for the MinIO binary store."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = data
        return f"saga-originals/{object_name}"

    async def get_object(self, object_name: str) -> bytes:
        return self.objects[object_name]

    async def remove_object(self, object_name: str) -> None:
        self.objects.pop(object_name, None)


class FakeQueue:
    """Records enqueued jobs instead of talking to Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, function: str, *args: object) -> None:
        self.jobs.append((function, args))


class FakeEmbedder:
    """Deterministic embedder for tests (no network)."""

    def __init__(self, dimension: int = 8) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7), *([0.0] * (self.dimension - 1))] for t in texts]

    async def aclose(self) -> None:
        return None


class InMemoryProjection:
    """In-memory stand-in for the OpenSearch projection (write side only)."""

    def __init__(self) -> None:
        self.projected: dict[str, dict[str, Any]] = {}

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        self.projected[document.document_id] = {
            "document": document,
            "folder_ancestor_ids": folder_ancestor_ids,
            "summary_embedding": summary_embedding,
        }

    async def delete_document(self, document_id: str) -> None:
        self.projected.pop(document_id, None)

    async def project_documents(self, records: list[ProjectionRecord]) -> list[str]:
        for record in records:
            await self.project_document(
                record.document,
                folder_ancestor_ids=record.folder_ancestor_ids,
                summary_embedding=record.summary_embedding,
            )
        return []

    async def document_ids(self) -> set[str]:
        return set(self.projected)

    async def refresh_documents(self) -> None:
        return None


class FakeSearch:
    """Db-backed ``SearchEngine`` implementation for API tests (no OpenSearch)."""

    def __init__(self, db: PostgresStore) -> None:
        self._db = db

    async def _all_documents(self) -> list[Document]:
        documents: list[Document] = []
        after: str | None = None
        while True:
            page, nxt = await self._db.scroll_documents(page_size=200, after_id=after)
            documents.extend(page)
            if not nxt:
                break
            after = nxt
        return documents

    async def _candidates(
        self,
        *,
        doc_type: str | None,
        folder_id: str | None,
        include_subtree: bool,
        title: str | None,
        status: str | None,
        filters: dict[str, str] | None,
        metadata: dict[str, str] | None = None,
    ) -> list[Document]:
        if folder_id is not None:
            documents, _ = await self._db.list_documents_in_folder(
                folder_id, include_subtree=include_subtree, page=1, page_size=1000
            )
        else:
            documents = await self._all_documents()

        def keep(doc: Document) -> bool:
            if doc_type is not None and doc.doc_type != doc_type:
                return False
            if title is not None and doc.title != title:
                return False
            if status is not None and doc.status.value != status:
                return False
            for key, value in (filters or {}).items():
                if not any(v.key == key and v.value == value for v in doc.extracted_values):
                    return False
            return all(doc.metadata.get(key) == value for key, value in (metadata or {}).items())

        return [doc for doc in documents if keep(doc)]

    async def hybrid_search(
        self,
        *,
        keyword_query: str | None = None,
        semantic_query: str | None = None,
        top_k: int | None = None,
        doc_type: str | None = None,
        folder_id: str | None = None,
        include_subtree: bool = True,
        title: str | None = None,
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        filters: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> HybridSearchResult:
        keyword = (keyword_query or "").strip()
        semantic = (semantic_query or "").strip()
        if not keyword and not semantic:
            raise ValidationError("Provide at least one of 'keyword_query' or 'semantic_query'.")
        documents = await self._candidates(
            doc_type=doc_type,
            folder_id=folder_id,
            include_subtree=include_subtree,
            title=title,
            status=status,
            filters=filters,
            metadata=metadata,
        )
        query = (keyword or semantic).lower()
        matched = [
            doc
            for doc in documents
            if not query
            or query in f"{doc.title} {doc.summary or ''} {doc.content_markdown or ''}".lower()
        ]
        limit = top_k or 10
        results = [
            SearchResultItem(
                document_id=doc.document_id,
                title=doc.title,
                score=1.0,
                doc_type=doc.doc_type,
                summary=doc.summary,
                folder_ids=doc.folder_ids,
                snippet=keyword or semantic,
            )
            for doc in matched[:limit]
        ]
        return HybridSearchResult(results=results)

    async def search_documents(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        page_size: int = 25,
        doc_type: str | None = None,
        folder_id: str | None = None,
        include_subtree: bool = True,
        title: str | None = None,
        status: str | None = None,
        filters: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[list[Document], int]:
        documents = await self._candidates(
            doc_type=doc_type,
            folder_id=folder_id,
            include_subtree=include_subtree,
            title=title,
            status=status,
            filters=filters,
            metadata=metadata,
        )
        if query:
            needle = query.lower()
            documents = [
                doc
                for doc in documents
                if needle
                in f"{doc.title} {doc.content_markdown or ''} {doc.doc_type or ''}".lower()
            ]
        total = len(documents)
        start = (page - 1) * page_size
        return documents[start : start + page_size], total

    async def get_document(self, document_id: str) -> Document | None:
        return await self._db.get_document(document_id)

    async def get_folder_tree(
        self, *, prefix: str | None = None, max_depth: int | None = None
    ) -> list[FolderNode]:
        return await self._db.folder_tree(prefix=prefix, max_depth=max_depth)

    async def list_documents_in_folder(
        self,
        *,
        folder_id: str,
        include_subtree: bool = True,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Document], int]:
        return await self._db.list_documents_in_folder(
            folder_id, include_subtree=include_subtree, page=page, page_size=page_size
        )


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def db() -> AsyncIterator[PostgresStore]:
    """A real PostgresStore backed by a temp-file SQLite database (aiosqlite)."""
    tmp = Path(tempfile.mkdtemp()) / "saga-test.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}", poolclass=NullPool)
    store = PostgresStore(AppConfig().postgres, engine=engine)
    await store.bootstrap()
    try:
        yield store
    finally:
        await store.close()
        tmp.unlink(missing_ok=True)


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.security.bearer_tokens = TEST_TOKEN
    return cfg


@pytest_asyncio.fixture
async def services(db: PostgresStore, config: AppConfig) -> Services:
    return Services(
        config=config,
        db=db,
        opensearch=InMemoryProjection(),
        minio=InMemoryBinaryStore(),
        queue=FakeQueue(),
        search=FakeSearch(db),
    )


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    app = create_app(services.config, services=services)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


async def seed_document(
    db: PostgresStore,
    *,
    title: str = "Doc.pdf",
    filename: str | None = None,
    content: str = "hello world",
    summary: str | None = None,
    status: DocumentStatus = DocumentStatus.READY,
    content_hash: str | None = None,
) -> Document:
    """Insert a document straight into the system of record for tests."""
    import uuid
    from datetime import UTC, datetime

    doc_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    document = Document(
        document_id=doc_id,
        title=title,
        filename=filename if filename is not None else title,
        mime_type="application/pdf",
        size_bytes=len(content.encode()),
        content_hash=content_hash or uuid.uuid4().hex,
        minio_object=f"saga-originals/{doc_id}",
        status=DocumentStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    await db.create_document(document)
    await db.update_content(doc_id, content)
    if summary is not None:
        await db.update_summary(doc_id, summary)
    await db.update_status(doc_id, status)
    fetched = await db.get_document(doc_id)
    assert fetched is not None
    return fetched
