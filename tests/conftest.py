"""Shared test fixtures: in-memory fakes and a configured API test client."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from docstore.api.app import create_app
from docstore.api.dependencies import Services
from docstore.core.config import AppConfig
from docstore.core.models import Document

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


@pytest.fixture
def services() -> Services:
    config = AppConfig()
    config.security.bearer_tokens = TEST_TOKEN
    return Services(
        config=config,
        opensearch=InMemoryDocumentStore(),
        minio=InMemoryBinaryStore(),
        queue=FakeQueue(),
    )


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    app = create_app(services.config, services=services)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
