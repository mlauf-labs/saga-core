from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService


class _Store:
    """Fake TimelineStore: records the folder_ids passed and returns canned events."""

    def __init__(self, parents: dict[str, str | None]) -> None:
        self._parents = parents
        self.seen_folder_ids: list[str] | None = None

    async def parents_map(self) -> dict[str, str | None]:
        return self._parents

    async def query_events(self, **kwargs: object) -> list[Event]:
        folder_ids = kwargs.get("folder_ids")
        self.seen_folder_ids = (
            list(cast("list[str]", folder_ids)) if folder_ids is not None else None
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
