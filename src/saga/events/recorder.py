"""Best-effort emission of audit events (timeline design).

The recorder builds typed :class:`Event` objects and writes them through an
:class:`EventSink`. Writing an event must NEVER fail the ingestion pipeline or an
API mutation, so every write is wrapped and failures are logged, not raised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from saga.core.logging import get_logger
from saga.core.models import Event, EventCategory, EventType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.models import FolderVote, SimilarDocument

_log = get_logger("saga.events")


class EventSink(Protocol):
    """The single write operation the recorder depends on (satisfied by PostgresStore)."""

    async def append_event(self, event: Event) -> bool: ...


class EventRecorder:
    """Builds and persists audit events. All methods are best-effort (never raise)."""

    def __init__(self, sink: EventSink, *, rationale_top_n: int = 5) -> None:
        self._sink = sink
        self._top_n = rationale_top_n

    async def _safe_append(self, event: Event) -> None:
        try:
            await self._sink.append_event(event)
        except Exception as exc:
            _log.warning("event_append_failed", event_type=str(event.event_type), error=str(exc))

    def _new(
        self,
        *,
        event_type: EventType,
        actor: str,
        summary: str,
        category: EventCategory = EventCategory.AUDIT,
        document_id: str | None = None,
        folder_id: str | None = None,
        dedupe_key: str | None = None,
        details: dict[str, object] | None = None,
    ) -> Event:
        now = datetime.now(UTC)
        return Event(
            event_id=uuid.uuid4().hex,
            category=category,
            event_type=event_type,
            document_id=document_id,
            folder_id=folder_id,
            occurred_at=now,  # audit: occurred == recorded
            recorded_at=now,
            actor=actor,
            summary=summary,
            dedupe_key=dedupe_key,
            details=dict(details or {}),
        )

    async def record_doc_ingested(self, *, document_id: str, actor: str = "pipeline") -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.DOC_INGESTED,
                actor=actor,
                summary="Document ingested.",
                document_id=document_id,
                dedupe_key=f"doc_ingested:{document_id}",
            )
        )

    async def record_placement(
        self,
        *,
        document_id: str,
        folders: Sequence[str],
        primary: str | None,
        similar: Sequence[SimilarDocument],
        votes: Sequence[FolderVote],
        reason: str | None = None,
        actor: str = "pipeline",
    ) -> None:
        if not folders:
            return
        top = list(similar)[: self._top_n]
        similar_titles = [s.title for s in top]
        because = f" — similar to {', '.join(repr(t) for t in similar_titles)}" if top else ""
        details: dict[str, object] = {
            "similar": [
                {"document_id": s.document_id, "title": s.title, "score": s.score} for s in top
            ],
            "votes": [{"folder_id": v.folder_id, "score": v.score} for v in votes],
            "assignments": list(folders),
            "primary": primary,
        }
        if reason:
            details["reason"] = reason
        await self._safe_append(
            self._new(
                event_type=EventType.PLACEMENT,
                actor=actor,
                summary=f"Placed in {len(folders)} folder(s){because}.",
                document_id=document_id,
                folder_id=primary,
                # No content-hash dedupe: the caller emits only on a real change, so a
                # later revert to a previously-seen folder set is still recorded.
                dedupe_key=None,
                details=details,
            )
        )

    async def record_reclassification(
        self,
        *,
        document_id: str,
        from_doc_type: str | None,
        to_doc_type: str,
        actor: str = "pipeline",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.RECLASSIFICATION,
                actor=actor,
                summary=f"Classified as '{to_doc_type}'"
                + (f" (was '{from_doc_type}')." if from_doc_type else "."),
                document_id=document_id,
                # No content-hash dedupe: the pipeline emits only on a genuine type
                # change, so a later revert to a prior type is still recorded.
                dedupe_key=None,
                details={"from_doc_type": from_doc_type, "to_doc_type": to_doc_type},
            )
        )

    async def record_move(
        self,
        *,
        document_id: str,
        from_folders: Sequence[str],
        to_folders: Sequence[str],
        primary: str | None,
        actor: str = "user",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.MOVE,
                actor=actor,
                summary=f"Moved to {len(to_folders)} folder(s).",
                document_id=document_id,
                folder_id=primary,
                dedupe_key=None,  # explicit moves are always recorded
                details={
                    "from_folders": list(from_folders),
                    "to_folders": list(to_folders),
                    "primary": primary,
                },
            )
        )

    async def record_folder_created(
        self,
        *,
        folder_id: str,
        name: str,
        parent_id: str | None,
        actor: str = "user",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.FOLDER_CREATED,
                actor=actor,
                summary=f"Folder '{name}' created.",
                folder_id=folder_id,
                dedupe_key=f"folder_created:{folder_id}",
                details={"name": name, "parent_id": parent_id},
            )
        )

    async def record_folder_renamed(
        self,
        *,
        folder_id: str,
        old_name: str,
        new_name: str,
        actor: str = "user",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.FOLDER_RENAMED,
                actor=actor,
                summary=f"Folder renamed from '{old_name}' to '{new_name}'.",
                folder_id=folder_id,
                dedupe_key=None,  # explicit renames are always recorded
                details={"old_name": old_name, "new_name": new_name},
            )
        )
