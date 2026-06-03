"""Shared test fixtures: in-memory fakes and a configured API test client."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from docstore.api.app import create_app
from docstore.api.dependencies import Services
from docstore.core.config import AppConfig
from docstore.core.models import CategoryNode, Document, SearchHit

TEST_TOKEN = "test-token"


class InMemoryDocumentStore:
    """In-memory stand-in for the OpenSearch document store."""

    def __init__(self) -> None:
        self.docs: dict[str, Document] = {}

    async def find_by_hash(self, content_hash: str) -> Document | None:
        return next((d for d in self.docs.values() if d.content_hash == content_hash), None)

    async def get_document(self, document_id: str) -> Document | None:
        return self.docs.get(document_id)

    async def index_document(self, document: Document) -> None:
        self.docs[document.document_id] = document

    async def delete_document(self, document_id: str) -> None:
        self.docs.pop(document_id, None)

    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]:
        ordered = sorted(self.docs.values(), key=lambda d: d.created_at, reverse=True)
        start = (page - 1) * page_size
        return ordered[start : start + page_size], len(ordered)

    async def scroll_documents(
        self, *, page_size: int, search_after: list[object] | None = None
    ) -> tuple[list[Document], list[object] | None]:
        ordered = sorted(self.docs.values(), key=lambda d: d.document_id)
        offset = int(str(search_after[0])) if search_after else 0
        page = ordered[offset : offset + page_size]
        next_cursor: list[object] | None = (
            [offset + page_size] if len(page) == page_size and page else None
        )
        return page, next_cursor


class InMemoryBinaryStore:
    """In-memory stand-in for the MinIO binary store."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = data
        return f"docstore-originals/{object_name}"

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


class FakeSearch:
    """In-memory stand-in for the search service."""

    def __init__(self, documents: dict[str, Document]) -> None:
        self._documents = documents

    async def hybrid_search(
        self,
        *,
        query: str,
        top_k: int | None = None,
        doc_type: str | None = None,
        category_path: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[SearchHit]:
        return [
            SearchHit(
                document_id=doc.document_id,
                chunk_id=f"{doc.document_id}:0",
                snippet=query,
                score=1.0,
                title=doc.title,
                doc_type=doc.doc_type,
                category_paths=doc.category_paths,
            )
            for doc in self._documents.values()
        ][: top_k or 10]

    async def get_category_tree(
        self, *, prefix: str | None = None, max_depth: int | None = None
    ) -> list[CategoryNode]:
        return [CategoryNode(path="Finance", name="Finance", document_count=1)]

    async def list_documents_in_category(
        self,
        *,
        category_path: str,
        include_subtree: bool = True,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Document], int]:
        docs = [d for d in self._documents.values() if category_path in d.category_paths]
        return docs, len(docs)


@pytest.fixture
def services() -> Services:
    config = AppConfig()
    config.security.bearer_tokens = TEST_TOKEN
    document_store = InMemoryDocumentStore()
    return Services(
        config=config,
        opensearch=document_store,
        minio=InMemoryBinaryStore(),
        queue=FakeQueue(),
        search=FakeSearch(document_store.docs),
    )


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    app = create_app(services.config, services=services)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
