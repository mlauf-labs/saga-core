"""API tests for doc-type CRUD and usage rules (FR-14)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from saga.storage.postgres import PostgresStore
from tests.conftest import seed_document


def test_doctypes_require_auth(client: TestClient) -> None:
    assert client.get("/doc-types").status_code == 401


def test_create_list_get_update_doc_type(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/doc-types",
        json={"name": "invoice", "description": "a bill"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    doc_type_id = created.json()["doc_type_id"]

    listing = client.get("/doc-types", headers=auth_headers).json()
    assert any(dt["doc_type_id"] == doc_type_id for dt in listing)

    fetched = client.get(f"/doc-types/{doc_type_id}", headers=auth_headers)
    assert fetched.json()["name"] == "invoice"

    updated = client.patch(
        f"/doc-types/{doc_type_id}",
        json={"description": "an updated bill"},
        headers=auth_headers,
    )
    assert updated.json()["description"] == "an updated bill"


def test_delete_unused_doc_type(client: TestClient, auth_headers: dict[str, str]) -> None:
    doc_type_id = client.post("/doc-types", json={"name": "memo"}, headers=auth_headers).json()[
        "doc_type_id"
    ]
    assert client.delete(f"/doc-types/{doc_type_id}", headers=auth_headers).status_code == 204


async def test_delete_in_use_doc_type_rejected(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc_type = await db.create_doc_type(name="contract")
    doc = await seed_document(db)
    await db.update_document(doc.document_id, doc_type_id=doc_type.doc_type_id)

    rejected = client.delete(f"/doc-types/{doc_type.doc_type_id}", headers=auth_headers)
    assert rejected.status_code == 409


async def test_list_documents_of_doc_type(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc_type = await db.create_doc_type(name="contract")
    doc = await seed_document(db)
    await db.update_document(doc.document_id, doc_type_id=doc_type.doc_type_id)

    response = client.get(f"/doc-types/{doc_type.doc_type_id}/documents", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == doc.document_id


def test_documents_of_missing_doc_type_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/doc-types/nope/documents", headers=auth_headers).status_code == 404
