from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from saga.api import service
from saga.core.models import DocType, Document, Folder, FolderRef


class _DB:
    async def get_document_folders(self, document_id: str) -> list[FolderRef]:
        return [FolderRef(folder_id="old", name="Old", is_primary=True)]

    async def set_document_folders(
        self,
        document_id: str,
        *,
        folder_ids: list[str],
        primary_id: str,
        assigned_by: str,
    ) -> list[FolderRef]:
        return [
            FolderRef(folder_id=fid, name=fid, is_primary=(fid == primary_id)) for fid in folder_ids
        ]

    async def get_folder(self, folder_id: str) -> Folder:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        return Folder(folder_id=folder_id, name="Old Name", created_at=now, updated_at=now)

    async def update_folder(self, folder_id: str, **kwargs: object) -> Folder:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        name = kwargs.get("name")
        new_name = name if isinstance(name, str) else "Old Name"
        return Folder(folder_id=folder_id, name=new_name, created_at=now, updated_at=now)


class _Events:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def record_move(self, **kw: Any) -> None:
        self.calls.append(("move", kw))

    async def record_folder_renamed(self, **kw: Any) -> None:
        self.calls.append(("folder_renamed", kw))

    async def record_document_deleted(self, **kw: Any) -> None:
        self.calls.append(("document_deleted", kw))

    async def record_folder_deleted(self, **kw: Any) -> None:
        self.calls.append(("folder_deleted", kw))

    async def record_doc_type_deleted(self, **kw: Any) -> None:
        self.calls.append(("doc_type_deleted", kw))


@pytest.fixture
def services_and_events() -> tuple[SimpleNamespace, _Events]:
    events = _Events()
    svc = SimpleNamespace(db=_DB(), events=events, opensearch=None, search=None)
    return svc, events


async def test_set_document_folders_records_move(
    services_and_events: tuple[SimpleNamespace, _Events], monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, events = services_and_events

    async def _noop_reproject(*a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(service, "reproject", _noop_reproject)
    await service.set_document_folders(
        svc,  # type: ignore[arg-type]
        "d1",
        folder_ids=["new"],
        primary_id="new",
        assigned_by="llm",
    )
    assert events.calls[0][0] == "move"
    assert events.calls[0][1]["actor"] == "agent"
    assert events.calls[0][1]["from_folders"] == ["old"]
    assert events.calls[0][1]["to_folders"] == ["new"]


async def test_update_folder_records_rename(
    services_and_events: tuple[SimpleNamespace, _Events], monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, events = services_and_events

    async def _noop_reproject(*a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(service, "_reproject_folder_subtree", _noop_reproject)
    await service.update_folder(svc, "f1", fields={"name": "New Name"})  # type: ignore[arg-type]
    assert events.calls[0][0] == "folder_renamed"
    assert events.calls[0][1]["old_name"] == "Old Name"
    assert events.calls[0][1]["new_name"] == "New Name"


async def test_delete_document_records_deletion() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    document = Document(
        document_id="d1",
        title="Invoice 2026",
        filename="inv.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="objects/d1",
        created_at=now,
        updated_at=now,
    )

    class _DocDB:
        async def get_document(self, document_id: str) -> Document:
            return document

        async def delete_document(self, document_id: str) -> None:
            return None

    class _Minio:
        async def remove_object(self, document_id: str) -> None:
            return None

    class _OpenSearch:
        async def delete_document(self, document_id: str) -> None:
            return None

    events = _Events()
    svc = SimpleNamespace(db=_DocDB(), events=events, minio=_Minio(), opensearch=_OpenSearch())
    await service.delete_document(svc, "d1")  # type: ignore[arg-type]
    assert events.calls[0][0] == "document_deleted"
    assert events.calls[0][1]["document_id"] == "d1"
    assert events.calls[0][1]["title"] == "Invoice 2026"


async def test_delete_folder_records_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    class _FolderDB:
        async def get_folder(self, folder_id: str) -> Folder:
            return Folder(folder_id=folder_id, name="Finance", created_at=now, updated_at=now)

        async def delete_folder(self, folder_id: str, *, strategy: str) -> list[str]:
            return ["doc1", "doc2"]

    async def _noop_reproject(*a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(service, "_reproject_affected", _noop_reproject)
    events = _Events()
    svc = SimpleNamespace(db=_FolderDB(), events=events)
    await service.delete_folder(svc, "f1", strategy="cascade")  # type: ignore[arg-type]
    assert events.calls[0][0] == "folder_deleted"
    assert events.calls[0][1]["name"] == "Finance"
    assert events.calls[0][1]["strategy"] == "cascade"
    assert events.calls[0][1]["affected"] == 2


async def test_delete_doc_type_records_deletion() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    class _DocTypeDB:
        async def get_doc_type(self, doc_type_id: str) -> DocType:
            return DocType(doc_type_id=doc_type_id, name="invoice", created_at=now, updated_at=now)

        async def delete_doc_type(self, doc_type_id: str) -> None:
            return None

    events = _Events()
    svc = SimpleNamespace(db=_DocTypeDB(), events=events)
    await service.delete_doc_type(svc, "t1")  # type: ignore[arg-type]
    assert events.calls[0][0] == "doc_type_deleted"
    assert events.calls[0][1]["doc_type_id"] == "t1"
    assert events.calls[0][1]["name"] == "invoice"
