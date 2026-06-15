"""API tests for the fused hybrid search endpoint (FR-19)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from saga.storage.postgres import PostgresStore
from tests.conftest import seed_document


def test_search_requires_auth(client: TestClient) -> None:
    assert client.post("/search", json={"keyword_query": "x"}).status_code == 401


async def test_search_keyword_returns_results(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db, title="invoice.pdf", content="annual invoice total")
    response = client.post("/search", json={"keyword_query": "invoice"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["document_id"] == doc.document_id


async def test_search_semantic_returns_results(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db, title="invoice.pdf", content="how much is the invoice")
    response = client.post("/search", json={"semantic_query": "invoice"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["document_id"] == doc.document_id


def test_search_requires_at_least_one_query(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post("/search", json={}, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


async def test_search_filters_by_folder(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    folder = await db.create_folder(name="Finance")
    other = await db.create_folder(name="Legal")
    inside = await seed_document(db, title="a.pdf", content="shared term")
    await db.add_document_folder(inside.document_id, folder.folder_id, primary=True)
    outside = await seed_document(db, title="b.pdf", content="shared term")
    await db.add_document_folder(outside.document_id, other.folder_id, primary=True)

    response = client.post(
        "/search",
        json={"keyword_query": "shared", "folder_id": folder.folder_id},
        headers=auth_headers,
    )
    assert response.status_code == 200
    ids = {item["document_id"] for item in response.json()["results"]}
    assert ids == {inside.document_id}
