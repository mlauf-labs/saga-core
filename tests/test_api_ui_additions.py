"""API tests for UI-facing additions: CORS, document search, metadata PATCH, file disposition."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from docstore.api.dependencies import Services
from docstore.core.models import Document, DocumentStatus, ExtractedValue


def _seed(services: Services, **overrides: object) -> Document:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "document_id": "d1",
        "title": "Invoice 2026.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "content_hash": "h1",
        "minio_object": "b/d1",
        "status": DocumentStatus.READY,
        "doc_type": "invoice",
        "content_markdown": "Annual liability premium",
        "extracted_values": [
            ExtractedValue(key="invoice_number", type="identifier", value="INV-1")
        ],
        "category_paths": ["Finance/Invoices"],
        "folder_structure": ["Finance/Invoices"],
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    doc = Document.model_validate(defaults)
    services.opensearch.docs[doc.document_id] = doc  # type: ignore[attr-defined]
    return doc


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


def test_document_search_by_query(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    response = client.post("/documents/search", json={"query": "liability"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == "d1"


def test_document_search_filter_by_title(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    hit = client.post(
        "/documents/search", json={"title": "Invoice 2026.pdf"}, headers=auth_headers
    ).json()
    miss = client.post(
        "/documents/search", json={"title": "Other.pdf"}, headers=auth_headers
    ).json()
    assert hit["total"] == 1
    assert miss["total"] == 0


def test_document_search_filter_by_category(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    body = client.post(
        "/documents/search", json={"category_path": "Finance"}, headers=auth_headers
    ).json()
    assert body["total"] == 1


# --- Metadata PATCH ---


def test_patch_metadata_updates_fields(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    response = client.patch(
        "/documents/d1/metadata",
        json={"doc_type": "contract", "category_paths": ["Legal/Contracts"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["doc_type"] == "contract"
    assert body["category_paths"] == ["Legal/Contracts"]
    # Unspecified field unchanged.
    assert body["folder_structure"] == ["Finance/Invoices"]


def test_patch_metadata_empty_rejected(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    response = client.patch("/documents/d1/metadata", json={}, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_patch_metadata_missing_document_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/documents/nope/metadata", json={"doc_type": "x"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_patch_metadata_requires_auth(client: TestClient) -> None:
    assert client.patch("/documents/d1/metadata", json={"doc_type": "x"}).status_code == 401


# --- Reanalyze ---


def test_reanalyze_requires_auth(client: TestClient) -> None:
    assert client.post("/documents/d1/reanalyze").status_code == 401


def test_reanalyze_enqueues_and_sets_pending(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    response = client.post("/documents/d1/reanalyze", headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["document_id"] == "d1"
    assert body["status"] == "pending"
    # Ingestion job re-enqueued and the stored status reset to pending.
    assert services.queue.jobs == [("ingest_document", ("d1",))]  # type: ignore[attr-defined]
    assert services.opensearch.docs["d1"].status.value == "pending"  # type: ignore[attr-defined]


def test_reanalyze_missing_document_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.post("/documents/nope/reanalyze", headers=auth_headers).status_code == 404


# --- File disposition ---


def test_file_default_attachment(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    services.minio.objects["d1"] = b"PDFDATA"  # type: ignore[attr-defined]
    response = client.get("/documents/d1/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


def test_file_inline_disposition(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    services.minio.objects["d1"] = b"PDFDATA"  # type: ignore[attr-defined]
    response = client.get("/documents/d1/file?disposition=inline", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline")


def test_file_invalid_disposition_422(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services)
    services.minio.objects["d1"] = b"PDFDATA"  # type: ignore[attr-defined]
    response = client.get("/documents/d1/file?disposition=bogus", headers=auth_headers)
    assert response.status_code == 422
