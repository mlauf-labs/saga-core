from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from saga.api import service
from saga.core.models import FolderRef


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


class _Events:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def record_move(self, **kw: Any) -> None:
        self.calls.append(("move", kw))


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
