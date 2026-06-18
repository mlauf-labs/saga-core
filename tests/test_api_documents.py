"""API tests for the documents endpoints (FR-1/10/11/12/13)."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from saga.api.dependencies import Services
from saga.storage.postgres import PostgresStore


def _upload(
    client: TestClient, headers: dict[str, str], content: bytes = b"hello world"
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/documents",
        headers=headers,
        files={"file": ("invoice.pdf", content, "application/pdf")},
    )
    return response


def test_upload_requires_auth(client: TestClient) -> None:
    response = _upload(client, headers={})
    assert response.status_code == 401
    assert response.json()["code"] == "auth_error"


def test_upload_accepts_and_enqueues(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    response = _upload(client, auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["title"] == "invoice.pdf"
    document_id = body["document_id"]
    # Job enqueued and binary stored.
    assert services.queue.jobs == [("ingest_document", (document_id,))]  # type: ignore[attr-defined]
    assert document_id in services.minio.objects  # type: ignore[attr-defined]


def test_upload_empty_file_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = _upload(client, auth_headers, content=b"")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_get_document(client: TestClient, auth_headers: dict[str, str]) -> None:
    document_id = _upload(client, auth_headers).json()["document_id"]
    response = client.get(f"/documents/{document_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["document_id"] == document_id


def test_get_missing_document_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/documents/does-not-exist", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_status_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    document_id = _upload(client, auth_headers).json()["document_id"]
    response = client.get(f"/documents/{document_id}/status", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_list_documents_paginated(client: TestClient, auth_headers: dict[str, str]) -> None:
    for i in range(3):
        _upload(client, auth_headers, content=f"doc-{i}".encode())
    response = client.get("/documents?page=1&page_size=2", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["items"][0]["content_markdown"] is None


def test_delete_document(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    document_id = _upload(client, auth_headers).json()["document_id"]
    response = client.delete(f"/documents/{document_id}", headers=auth_headers)
    assert response.status_code == 204
    assert document_id not in services.minio.objects  # type: ignore[attr-defined]
    assert client.get(f"/documents/{document_id}", headers=auth_headers).status_code == 404


def test_delete_missing_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.delete("/documents/nope", headers=auth_headers).status_code == 404


async def test_dedup_replace_keeps_single_document(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    # Default dedup policy is "replace".
    first = _upload(client, auth_headers, content=b"same").json()["document_id"]
    second = _upload(client, auth_headers, content=b"same").json()["document_id"]
    assert first != second
    documents, total = await db.list_documents(page=1, page_size=10)
    assert total == 1
    assert {d.document_id for d in documents} == {second}


def test_dedup_reject(client: TestClient, auth_headers: dict[str, str], services: Services) -> None:
    services.config.dedup.on_duplicate = "reject"
    _upload(client, auth_headers, content=b"same")
    response = _upload(client, auth_headers, content=b"same")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_replace_document_keeps_id(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    document_id = _upload(client, auth_headers, content=b"v1").json()["document_id"]
    response = client.put(
        f"/documents/{document_id}",
        headers=auth_headers,
        files={"file": ("invoice.pdf", b"v2", "application/pdf")},
    )
    assert response.status_code == 202
    assert response.json()["document_id"] == document_id
    stored = await db.get_document(document_id)
    assert stored is not None
    assert stored.content_hash != ""


def test_patch_sets_metadata(client: TestClient, auth_headers: dict[str, str]) -> None:
    document_id = _upload(client, auth_headers).json()["document_id"]
    response = client.patch(
        f"/documents/{document_id}",
        json={"metadata": {"project": "Apollo"}},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["metadata"] == {"project": "Apollo"}


def test_patch_rejects_reserved_metadata_key(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    document_id = _upload(client, auth_headers).json()["document_id"]
    response = client.patch(
        f"/documents/{document_id}",
        json={"metadata": {"saga_id": "x"}},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
