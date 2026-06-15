"""API tests for folder CRUD, tree, notes and browsing (FR-16/22)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from saga.storage.postgres import PostgresStore
from tests.conftest import seed_document


def test_folders_require_auth(client: TestClient) -> None:
    assert client.get("/folders").status_code == 401


def test_create_and_get_folder_tree(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/folders",
        json={"name": "Finance", "description": "money", "metadata": {"team": "ops"}},
        headers=auth_headers,
    )
    assert created.status_code == 201
    folder_id = created.json()["folder_id"]

    sub = client.post(
        "/folders",
        json={"name": "Invoices", "parent_id": folder_id},
        headers=auth_headers,
    )
    assert sub.status_code == 201

    tree = client.get("/folders", headers=auth_headers).json()
    root = next(node for node in tree if node["folder_id"] == folder_id)
    assert root["name"] == "Finance"
    assert root["children"][0]["name"] == "Invoices"


def test_get_update_folder(client: TestClient, auth_headers: dict[str, str]) -> None:
    folder_id = client.post(
        "/folders", json={"name": "Legal"}, headers=auth_headers
    ).json()["folder_id"]

    fetched = client.get(f"/folders/{folder_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Legal"

    updated = client.patch(
        f"/folders/{folder_id}",
        json={"name": "Contracts", "description": "legal docs"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Contracts"
    assert updated.json()["description"] == "legal docs"


def test_get_missing_folder_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/folders/nope", headers=auth_headers).status_code == 404


async def test_documents_in_folder(
    client: TestClient, auth_headers: dict[str, str], db: PostgresStore
) -> None:
    folder = await db.create_folder(name="Finance")
    doc = await seed_document(db, title="a.pdf")
    await db.add_document_folder(doc.document_id, folder.folder_id, primary=True)

    response = client.get(f"/folders/{folder.folder_id}/documents", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == doc.document_id


def test_folder_notes_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    folder_id = client.post(
        "/folders", json={"name": "Notes"}, headers=auth_headers
    ).json()["folder_id"]

    note = client.post(
        f"/folders/{folder_id}/notes", json={"content": "hello"}, headers=auth_headers
    )
    assert note.status_code == 201
    note_id = note.json()["note_id"]

    listing = client.get(f"/folders/{folder_id}/notes", headers=auth_headers).json()
    assert [n["note_id"] for n in listing] == [note_id]

    updated = client.patch(
        f"/folders/{folder_id}/notes/{note_id}",
        json={"content": "changed"},
        headers=auth_headers,
    )
    assert updated.json()["content"] == "changed"

    deleted = client.delete(
        f"/folders/{folder_id}/notes/{note_id}", headers=auth_headers
    )
    assert deleted.status_code == 204
    assert client.get(f"/folders/{folder_id}/notes", headers=auth_headers).json() == []


def test_delete_folder_reject_when_not_empty(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    parent = client.post(
        "/folders", json={"name": "Parent"}, headers=auth_headers
    ).json()["folder_id"]
    client.post("/folders", json={"name": "Child", "parent_id": parent}, headers=auth_headers)

    rejected = client.delete(f"/folders/{parent}", headers=auth_headers)
    assert rejected.status_code == 409


def test_delete_folder_cascade(client: TestClient, auth_headers: dict[str, str]) -> None:
    parent = client.post(
        "/folders", json={"name": "Parent"}, headers=auth_headers
    ).json()["folder_id"]
    child = client.post(
        "/folders", json={"name": "Child", "parent_id": parent}, headers=auth_headers
    ).json()["folder_id"]

    deleted = client.delete(f"/folders/{parent}?strategy=cascade", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/folders/{parent}", headers=auth_headers).status_code == 404
    assert client.get(f"/folders/{child}", headers=auth_headers).status_code == 404


def test_delete_folder_reparent(client: TestClient, auth_headers: dict[str, str]) -> None:
    parent = client.post(
        "/folders", json={"name": "Parent"}, headers=auth_headers
    ).json()["folder_id"]
    child = client.post(
        "/folders", json={"name": "Child", "parent_id": parent}, headers=auth_headers
    ).json()["folder_id"]

    deleted = client.delete(f"/folders/{parent}?strategy=reparent", headers=auth_headers)
    assert deleted.status_code == 204
    # The child survived and is now a root folder.
    moved = client.get(f"/folders/{child}", headers=auth_headers).json()
    assert moved["parent_id"] is None
