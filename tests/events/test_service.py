from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Store:
    """Fake TimelineStore: records the scope args passed and returns a canned event."""

    def __init__(
        self, parents: dict[str, str | None], *, member_doc_ids: list[str] | None = None
    ) -> None:
        self._parents = parents
        self._member_doc_ids = member_doc_ids or []
        self.seen_folder_ids: list[str] | None = None
        self.seen_document_ids: list[str] | None = None

    async def parents_map(self) -> dict[str, str | None]:
        return self._parents

    async def document_ids_in_folders(self, folder_ids: Sequence[str]) -> list[str]:
        return list(self._member_doc_ids)

    async def query_events(self, **kwargs: object) -> list[Event]:
        folder_ids = kwargs.get("folder_ids")
        self.seen_folder_ids = (
            list(cast("list[str]", folder_ids)) if folder_ids is not None else None
        )
        document_ids = kwargs.get("document_ids")
        self.seen_document_ids = (
            list(cast("list[str]", document_ids)) if document_ids is not None else None
        )
        now = datetime(2026, 5, 1, tzinfo=UTC)
        return [
            Event(
                event_id="e1",
                category=EventCategory.AUDIT,
                event_type=EventType.PLACEMENT,
                recorded_at=now,
                actor="pipeline",
                summary="s",
            )
        ]


async def test_query_expands_folder_subtree() -> None:
    store = _Store({"f_root": None, "f_child": "f_root"})
    service = TimelineService(store)
    await service.query(EventQuery(folder_id="f_root", include_subtree=True))
    assert store.seen_folder_ids is not None
    assert sorted(store.seen_folder_ids) == ["f_child", "f_root"]


async def test_query_without_subtree_uses_single_folder() -> None:
    store = _Store({"f_root": None, "f_child": "f_root"})
    service = TimelineService(store)
    await service.query(EventQuery(folder_id="f_root", include_subtree=False))
    assert store.seen_folder_ids == ["f_root"]


async def test_query_includes_documents_in_folder_scope() -> None:
    # Document-level events are matched via current folder membership (design §6.2),
    # so a document in the folder is included even though events store only the primary.
    store = _Store({"f_root": None}, member_doc_ids=["doc-in-folder"])
    service = TimelineService(store)
    await service.query(EventQuery(folder_id="f_root"))
    assert store.seen_document_ids == ["doc-in-folder"]


async def test_query_without_folder_scopes_nothing() -> None:
    store = _Store({"f_root": None}, member_doc_ids=["x"])
    service = TimelineService(store)
    await service.query(EventQuery(document_id="d1"))
    assert store.seen_folder_ids is None
    assert store.seen_document_ids is None
