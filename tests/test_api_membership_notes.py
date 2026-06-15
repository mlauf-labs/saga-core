"""API tests for document notes and document<->folder membership (FR-16)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from saga.api.dependencies import Services
from saga.storage.postgres import PostgresStore
from tests.conftest import seed_document


async def test_document_notes_crud(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db)
    note = client.post(
        f"/documents/{doc.document_id}/notes",
        json={"content": "follow up"},
        headers=auth_headers,
    )
    assert note.status_code == 201
    note_id = note.json()["note_id"]

    listing = client.get(f"/documents/{doc.document_id}/notes", headers=auth_headers).json()
    assert [n["note_id"] for n in listing] == [note_id]

    updated = client.patch(
        f"/documents/{doc.document_id}/notes/{note_id}",
        json={"content": "done"},
        headers=auth_headers,
    )
    assert updated.json()["content"] == "done"

    deleted = client.delete(
        f"/documents/{doc.document_id}/notes/{note_id}", headers=auth_headers
    )
    assert deleted.status_code == 204


async def test_membership_add_set_primary_remove(
    client: TestClient, auth_headers: dict[str, str], services: Services, db: PostgresStore
) -> None:
    doc = await seed_document(db)
    a = await db.create_folder(name="A")
    b = await db.create_folder(name="B")

    added = client.post(
        f"/documents/{doc.document_id}/folders/{a.folder_id}?primary=true", headers=auth_headers
    )
    assert added.status_code == 200
    assert {f["folder_id"] for f in added.json()["folders"]} == {a.folder_id}
    # Membership mutations re-project to the search index.
    assert doc.document_id in services.opensearch.projected  # type: ignore[attr-defined]

    replaced = client.put(
        f"/documents/{doc.document_id}/folders",
        json={"folder_ids": [a.folder_id, b.folder_id], "primary_id": b.folder_id},
        headers=auth_headers,
    )
    folders = {f["folder_id"]: f["is_primary"] for f in replaced.json()["folders"]}
    assert folders == {a.folder_id: False, b.folder_id: True}

    primary = client.patch(
        f"/documents/{doc.document_id}/folders/{a.folder_id}", headers=auth_headers
    )
    primary_map = {f["folder_id"]: f["is_primary"] for f in primary.json()["folders"]}
    assert primary_map[a.folder_id] is True
    assert primary_map[b.folder_id] is False

    removed = client.delete(
        f"/documents/{doc.document_id}/folders/{b.folder_id}", headers=auth_headers
    )
    assert {f["folder_id"] for f in removed.json()["folders"]} == {a.folder_id}


async def test_list_document_folders(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    doc = await seed_document(db)
    folder = await db.create_folder(name="A")
    await db.add_document_folder(doc.document_id, folder.folder_id, primary=True)

    response = client.get(f"/documents/{doc.document_id}/folders", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["folders"][0]["folder_id"] == folder.folder_id
