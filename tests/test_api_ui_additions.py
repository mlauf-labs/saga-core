"""API tests for UI-facing behaviour: CORS, document search, PATCH, file disposition."""

from __future__ import annotations

from fastapi.testclient import TestClient

from saga.api.dependencies import Services
from saga.core.models import DocumentStatus
from saga.storage.postgres import PostgresStore
from tests.conftest import seed_document

# --- CORS ---


def test_cors_preflight_allowed_by_default(client: TestClient) -> None:
    response = client.options(
        "/documents/search",
        headers={
            "Origin": "https://ui.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in response.headers}


def test_cors_header_on_response(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/health", headers={**auth_headers, "Origin": "https://ui.example.com"})
    assert response.headers.get("access-control-allow-origin") in {"*", "https://ui.example.com"}


# --- Document search ---


def test_document_search_requires_auth(client: TestClient) -> None:
    assert client.post("/documents/search", json={"query": "x"}).status_code == 401


async def test_document_search_by_query(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db, title="Invoice 2026.pdf", content="annual liability premium")
    response = client.post("/documents/search", json={"query": "liability"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == doc.document_id


async def test_document_search_filter_by_title(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    await seed_document(db, title="Invoice 2026.pdf", content="x")
    hit = client.post(
        "/documents/search", json={"title": "Invoice 2026.pdf"}, headers=auth_headers
    ).json()
    miss = client.post(
        "/documents/search", json={"title": "Other.pdf"}, headers=auth_headers
    ).json()
    assert hit["total"] == 1
    assert miss["total"] == 0


async def test_document_search_filter_by_folder(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    folder = await db.create_folder(name="Finance")
    doc = await seed_document(db, title="a.pdf", content="x")
    await db.add_document_folder(doc.document_id, folder.folder_id, primary=True)
    body = client.post(
        "/documents/search", json={"folder_id": folder.folder_id}, headers=auth_headers
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == doc.document_id


# --- PATCH editable fields ---


async def test_patch_document_updates_fields(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db, title="Invoice 2026.pdf", content="x", summary="old summary")
    doc_type = await db.create_doc_type(name="contract")
    response = client.patch(
        f"/documents/{doc.document_id}",
        json={"summary": "new summary", "doc_type_id": doc_type.doc_type_id},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "new summary"
    assert body["doc_type"] == "contract"
    # Untouched field stays.
    assert body["title"] == "Invoice 2026.pdf"


async def test_patch_document_empty_rejected(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db)
    response = client.patch(f"/documents/{doc.document_id}", json={}, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_patch_document_missing_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.patch("/documents/nope", json={"summary": "x"}, headers=auth_headers)
    assert response.status_code == 404


def test_patch_document_requires_auth(client: TestClient) -> None:
    assert client.patch("/documents/d1", json={"summary": "x"}).status_code == 401


# --- Reanalyze ---


def test_reanalyze_requires_auth(client: TestClient) -> None:
    assert client.post("/documents/d1/reanalyze").status_code == 401


async def test_reanalyze_enqueues_and_sets_pending(
    client: TestClient, auth_headers: dict[str, str], services: Services, db: PostgresStore
) -> None:
    doc = await seed_document(db)
    response = client.post(f"/documents/{doc.document_id}/reanalyze", headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["document_id"] == doc.document_id
    assert body["status"] == "pending"
    assert services.queue.jobs == [("ingest_document", (doc.document_id,))]  # type: ignore[attr-defined]
    refreshed = await db.get_document(doc.document_id)
    assert refreshed is not None
    assert refreshed.status is DocumentStatus.PENDING


def test_reanalyze_missing_document_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.post("/documents/nope/reanalyze", headers=auth_headers).status_code == 404


# --- File disposition ---


async def test_file_default_attachment(
    client: TestClient, auth_headers: dict[str, str], services: Services, db: PostgresStore
) -> None:
    doc = await seed_document(db)
    services.minio.objects[doc.document_id] = b"PDFDATA"  # type: ignore[attr-defined]
    response = client.get(f"/documents/{doc.document_id}/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


async def test_file_inline_disposition(
    client: TestClient, auth_headers: dict[str, str], services: Services, db: PostgresStore
) -> None:
    doc = await seed_document(db)
    services.minio.objects[doc.document_id] = b"PDFDATA"  # type: ignore[attr-defined]
    response = client.get(
        f"/documents/{doc.document_id}/file?disposition=inline", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline")


async def test_file_invalid_disposition_422(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db)
    response = client.get(
        f"/documents/{doc.document_id}/file?disposition=bogus", headers=auth_headers
    )
    assert response.status_code == 422
