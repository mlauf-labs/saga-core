"""API tests for the search and category endpoints (FR-19/22)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from docstore.api.dependencies import Services
from docstore.core.models import Document, DocumentStatus


def _seed(services: Services, *, category: str = "Finance/Invoices") -> str:
    now = datetime.now(UTC)
    doc = Document(
        document_id="d1",
        title="invoice.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="b/d1",
        status=DocumentStatus.READY,
        doc_type="invoice",
        category_paths=[category],
        created_at=now,
        updated_at=now,
    )
    services.opensearch.docs[doc.document_id] = doc  # type: ignore[attr-defined]
    return doc.document_id


def test_search_requires_auth(client: TestClient) -> None:
    assert client.post("/search", json={"query": "x"}).status_code == 401


def test_search_returns_hits(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    response = client.post("/search", json={"query": "invoice"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "invoice"
    assert body["hits"][0]["document_id"] == "d1"


def test_search_empty_query_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/search", json={"query": ""}, headers=auth_headers)
    assert response.status_code == 422


def test_category_tree(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/categories/tree", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["tree"][0]["name"] == "Finance"


def test_documents_in_category(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services, category="Finance/Invoices")
    response = client.get("/categories/Finance/Invoices/documents", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == "d1"
