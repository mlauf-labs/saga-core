from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from saga.api import service
from saga.core.models import Folder, FolderRef


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
