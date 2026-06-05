"""Shared test fixtures: in-memory fakes and a configured API test client."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from docstore.api.app import create_app
from docstore.api.dependencies import Services
from docstore.core.config import AppConfig
from docstore.core.errors import NotFoundError
from docstore.core.models import CategoryNode, Document, DocumentStatus, ExtractedValue, SearchHit

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

    async def update_status(
        self, document_id: str, status: DocumentStatus, error: str | None = None
    ) -> None:
        doc = self.docs.get(document_id)
        if doc is not None:
            self.docs[document_id] = doc.model_copy(update={"status": status, "error": error})

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

    async def search_documents(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
        doc_type: str | None = None,
        category_path: str | None = None,
        title: str | None = None,
        status: str | None = None,
        extracted_values: dict[str, str] | None = None,
    ) -> tuple[list[Document], int]:
        def matches(doc: Document) -> bool:
            if query and query.lower() not in (
                f"{doc.title} {doc.content_markdown or ''} {doc.doc_type or ''}".lower()
            ):
                return False
            if doc_type and doc.doc_type != doc_type:
                return False
            if title and doc.title != title:
                return False
            if status and doc.status.value != status:
                return False
            if category_path and not any(
                p == category_path or p.startswith(f"{category_path}/") for p in doc.category_paths
            ):
                return False
            for key, value in (extracted_values or {}).items():
                if not any(v.key == key and v.value == value for v in doc.extracted_values):
                    return False
            return True

        hits = [d for d in self.docs.values() if matches(d)]
        start = (page - 1) * page_size
        return hits[start : start + page_size], len(hits)

    async def update_document_fields(
        self,
        document_id: str,
        *,
        doc_type: str | None = None,
        extracted_values: list[ExtractedValue] | None = None,
        folder_structure: list[str] | None = None,
        category_paths: list[str] | None = None,
    ) -> Document:
        doc = self.docs.get(document_id)
        if doc is None:
            raise NotFoundError(f"Document '{document_id}' was not found.")
        update: dict[str, object] = {}
        if doc_type is not None:
            update["doc_type"] = doc_type
        if extracted_values is not None:
            update["extracted_values"] = extracted_values
        if folder_structure is not None:
            update["folder_structure"] = folder_structure
        if category_paths is not None:
            update["category_paths"] = category_paths
        updated = doc.model_copy(update=update)
        self.docs[document_id] = updated
        return updated


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
    """In-memory stand-in for the search service (shares the document store dict)."""

    def __init__(self, store: InMemoryDocumentStore) -> None:
        self._store = store
        self._documents = store.docs

    async def hybrid_search(
        self,
        *,
        query: str,
        top_k: int | None = None,
        doc_type: str | None = None,
        category_path: str | None = None,
        title: str | None = None,
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
            if (title is None or doc.title == title)
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

    async def search_documents(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        page_size: int = 25,
        doc_type: str | None = None,
        category_path: str | None = None,
        title: str | None = None,
        status: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> tuple[list[Document], int]:
        return await self._store.search_documents(
            query=query,
            page=page,
            page_size=page_size,
            doc_type=doc_type,
            category_path=category_path,
            title=title,
            status=status,
            extracted_values=filters,
        )

    async def update_document_metadata(
        self,
        document_id: str,
        *,
        doc_type: str | None = None,
        extracted_values: list[ExtractedValue] | None = None,
        folder_structure: list[str] | None = None,
        category_paths: list[str] | None = None,
    ) -> Document:
        return await self._store.update_document_fields(
            document_id,
            doc_type=doc_type,
            extracted_values=extracted_values,
            folder_structure=folder_structure,
            category_paths=category_paths,
        )


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
        search=FakeSearch(document_store),
    )


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    app = create_app(services.config, services=services)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
