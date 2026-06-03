"""API tests for the export endpoint and binary download (FR-28)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from docstore.api.dependencies import Services
from docstore.core.models import Document, DocumentStatus


def _seed(services: Services, count: int) -> None:
    now = datetime.now(UTC)
    for i in range(count):
        doc = Document(
            document_id=f"d{i:02d}",
            title=f"doc{i}.pdf",
            mime_type="application/pdf",
            size_bytes=3,
            content_hash=f"h{i}",
            minio_object=f"b/d{i:02d}",
            status=DocumentStatus.READY,
            content_markdown=f"# Doc {i}",
            folder_structure=["Finance/Invoices"],
            created_at=now,
            updated_at=now,
        )
        services.opensearch.docs[doc.document_id] = doc  # type: ignore[attr-defined]
        services.minio.objects[doc.document_id] = f"binary-{i}".encode()  # type: ignore[attr-defined]


def test_export_requires_auth(client: TestClient) -> None:
    assert client.get("/export/documents").status_code == 401


def test_export_paginates_with_cursor(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services, 5)
    first = client.get("/export/documents?page_size=2", headers=auth_headers).json()
    assert len(first["items"]) == 2
    assert first["items"][0]["content_markdown"].startswith("# Doc")
    assert first["next_cursor"] is not None

    # Walk all pages.
    seen = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = client.get(
            f"/export/documents?page_size=2&cursor={cursor}", headers=auth_headers
        ).json()
        seen.extend(page["items"])
        cursor = page["next_cursor"]
    assert len(seen) == 5


def test_export_invalid_cursor_400(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/export/documents?cursor=@@@", headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_download_document_file(
    client: TestClient, auth_headers: dict[str, str], services: Services
) -> None:
    _seed(services, 1)
    response = client.get("/documents/d00/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.content == b"binary-0"
    assert "attachment" in response.headers["content-disposition"]


def test_download_missing_document_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/documents/nope/file", headers=auth_headers).status_code == 404
