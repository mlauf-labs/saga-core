"""API tests for the export endpoint and binary download (FR-28)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from saga.api.dependencies import Services
from saga.storage.postgres import PostgresStore
from tests.conftest import seed_document


async def _seed(db: PostgresStore, services: Services, count: int) -> list[str]:
    folder = await db.create_folder(name="Finance")
    sub = await db.create_folder(name="Invoices", parent_id=folder.folder_id)
    ids: list[str] = []
    for i in range(count):
        doc = await seed_document(db, title=f"doc{i}.pdf", content=f"# Doc {i}")
        await db.add_document_folder(doc.document_id, sub.folder_id, primary=True)
        services.minio.objects[doc.document_id] = f"binary-{i}".encode()  # type: ignore[attr-defined]
        ids.append(doc.document_id)
    return ids


def test_export_requires_auth(client: TestClient) -> None:
    assert client.get("/export/documents").status_code == 401


async def test_export_paginates_with_cursor(
    client: TestClient, auth_headers: dict[str, str], services: Services, db: PostgresStore
) -> None:
    await _seed(db, services, 5)
    first = client.get("/export/documents?page_size=2", headers=auth_headers).json()
    assert len(first["items"]) == 2
    assert first["items"][0]["content_markdown"].startswith("# Doc")
    # Primary folder path is exposed for the backup layout.
    assert first["items"][0]["primary_folder_path"] == ["Finance", "Invoices"]
    assert first["next_cursor"] is not None

    seen = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = client.get(
            f"/export/documents?page_size=2&cursor={cursor}", headers=auth_headers
        ).json()
        seen.extend(page["items"])
        cursor = page["next_cursor"]
    assert len({item["document_id"] for item in seen}) == 5


async def test_download_document_file(
    client: TestClient, auth_headers: dict[str, str], services: Services, db: PostgresStore
) -> None:
    ids = await _seed(db, services, 1)
    response = client.get(f"/documents/{ids[0]}/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.content == b"binary-0"
    assert "attachment" in response.headers["content-disposition"]


def test_download_missing_document_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/documents/nope/file", headers=auth_headers).status_code == 404
