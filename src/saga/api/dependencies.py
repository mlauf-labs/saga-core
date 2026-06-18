"""Dependency wiring for the REST API: the service container and auth (FR-35)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from saga.api.auth import verify_bearer_token
from saga.core.errors import SagaError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from saga.core.config import AppConfig
    from saga.core.models import (
        DocType,
        Document,
        DocumentStatus,
        Event,
        ExtractedValue,
        Folder,
        FolderNode,
        FolderRef,
        HybridSearchResult,
        Note,
    )
    from saga.events import EventRecorder, TimelineService


class Database(Protocol):
    """The relational system-of-record operations the API depends on (DIP)."""

    # documents
    async def find_by_hash(self, content_hash: str) -> Document | None: ...
    async def create_document(self, document: Document) -> Document: ...
    async def get_document(self, document_id: str) -> Document | None: ...
    async def get_document_titles(self, document_ids: list[str]) -> dict[str, str]: ...
    async def delete_document(self, document_id: str) -> None: ...
    async def update_status(
        self, document_id: str, status: DocumentStatus, error: str | None = ...
    ) -> None: ...
    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]: ...
    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = ...
    ) -> tuple[list[Document], str | None]: ...
    async def update_document(
        self,
        document_id: str,
        *,
        title: str | None = ...,
        summary: str | None = ...,
        doc_type_id: str | None = ...,
        clear_doc_type: bool = ...,
        extracted_values: list[ExtractedValue] | None = ...,
        metadata: dict[str, str] | None = ...,
    ) -> Document: ...
    async def get_summary_embedding(self, document_id: str) -> list[float] | None: ...
    async def list_documents_by_doc_type(
        self, doc_type_id: str, *, page: int, page_size: int
    ) -> tuple[list[Document], int]: ...
    async def list_documents_in_folder(
        self, folder_id: str, *, include_subtree: bool, page: int, page_size: int
    ) -> tuple[list[Document], int]: ...
    # document notes
    async def add_document_note(self, document_id: str, content: str) -> Note: ...
    async def update_document_note(self, note_id: str, content: str) -> Note: ...
    async def delete_document_note(self, note_id: str) -> None: ...
    # folders
    async def list_folders(self) -> list[Folder]: ...
    async def get_folder(self, folder_id: str) -> Folder | None: ...
    async def parents_map(self) -> dict[str, str | None]: ...
    async def folder_path(self, folder_id: str) -> list[str]: ...
    async def create_folder(
        self,
        *,
        name: str,
        description: str | None = ...,
        parent_id: str | None = ...,
        metadata: dict[str, str] | None = ...,
        emoji: str | None = ...,
    ) -> Folder: ...
    async def update_folder(
        self,
        folder_id: str,
        *,
        name: str | None = ...,
        description: str | None = ...,
        parent_id: str | None = ...,
        clear_parent: bool = ...,
        metadata: dict[str, str] | None = ...,
        emoji: str | None = ...,
    ) -> Folder: ...
    async def delete_folder(self, folder_id: str, *, strategy: str = ...) -> list[str]: ...
    async def folder_tree(
        self, *, prefix: str | None = ..., max_depth: int | None = ...
    ) -> list[FolderNode]: ...
    # folder notes
    async def add_folder_note(self, folder_id: str, content: str) -> Note: ...
    async def update_folder_note(self, note_id: str, content: str) -> Note: ...
    async def delete_folder_note(self, note_id: str) -> None: ...
    # doc-types
    async def list_doc_types(self) -> list[DocType]: ...
    async def get_doc_type(self, doc_type_id: str) -> DocType | None: ...
    async def get_doc_type_by_name(self, name: str) -> DocType | None: ...
    async def create_doc_type(
        self, *, name: str, description: str | None = ..., emoji: str | None = ...
    ) -> DocType: ...
    async def update_doc_type(
        self,
        doc_type_id: str,
        *,
        name: str | None = ...,
        description: str | None = ...,
        emoji: str | None = ...,
    ) -> DocType: ...
    async def delete_doc_type(self, doc_type_id: str) -> None: ...
    # membership
    async def get_document_folders(self, document_id: str) -> list[FolderRef]: ...
    async def set_document_folders(
        self,
        document_id: str,
        *,
        folder_ids: Sequence[str],
        primary_id: str | None = ...,
        assigned_by: str = ...,
    ) -> list[FolderRef]: ...
    async def add_document_folder(
        self,
        document_id: str,
        folder_id: str,
        *,
        primary: bool = ...,
        assigned_by: str = ...,
    ) -> list[FolderRef]: ...
    async def remove_document_folder(self, document_id: str, folder_id: str) -> list[FolderRef]: ...
    async def set_primary_folder(self, document_id: str, folder_id: str) -> list[FolderRef]: ...
    # event mutations (EventMutationStore + EventSink)
    async def append_event(self, event: Event) -> bool: ...
    async def get_event(self, event_id: str) -> Event | None: ...
    async def delete_event(self, event_id: str) -> bool: ...
    async def update_event(
        self,
        event_id: str,
        *,
        summary: str | None = ...,
        occurred_at: datetime | None = ...,
        confidence: float | None = ...,
        details: dict[str, Any] | None = ...,
    ) -> Event | None: ...
    async def merge_events(
        self, canonical_id: str, duplicate_ids: Sequence[str]
    ) -> Event | None: ...


class ProjectionStore(Protocol):
    """The OpenSearch search-projection operations the API depends on (FR-25)."""

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = ...,
    ) -> None: ...
    async def delete_document(self, document_id: str) -> None: ...


class BinaryStore(Protocol):
    """The object-storage operations the API depends on."""

    async def put_object(self, object_name: str, data: bytes, content_type: str) -> str: ...
    async def get_object(self, object_name: str) -> bytes: ...
    async def remove_object(self, object_name: str) -> None: ...


class JobQueue(Protocol):
    """Minimal interface for enqueueing background jobs (ARQ ``ArqRedis``)."""

    async def enqueue_job(self, function: str, *args: Any) -> Any: ...  # noqa: ANN401


class SearchEngine(Protocol):
    """Search operations the API depends on (FR-19/22)."""

    async def hybrid_search(
        self,
        *,
        keyword_query: str | None = ...,
        semantic_query: str | None = ...,
        top_k: int | None = ...,
        doc_type: str | None = ...,
        folder_id: str | None = ...,
        include_subtree: bool = ...,
        title: str | None = ...,
        status: str | None = ...,
        created_from: str | None = ...,
        created_to: str | None = ...,
        filters: dict[str, str] | None = ...,
        metadata: dict[str, str] | None = ...,
    ) -> HybridSearchResult: ...

    async def get_document(self, document_id: str) -> Document | None: ...

    async def get_folder_tree(
        self, *, prefix: str | None = ..., max_depth: int | None = ...
    ) -> list[FolderNode]: ...

    async def list_documents_in_folder(
        self,
        *,
        folder_id: str,
        include_subtree: bool = ...,
        page: int = ...,
        page_size: int = ...,
    ) -> tuple[list[Document], int]: ...

    async def search_documents(
        self,
        *,
        query: str | None = ...,
        page: int = ...,
        page_size: int = ...,
        doc_type: str | None = ...,
        folder_id: str | None = ...,
        include_subtree: bool = ...,
        title: str | None = ...,
        status: str | None = ...,
        filters: dict[str, str] | None = ...,
        metadata: dict[str, str] | None = ...,
    ) -> tuple[list[Document], int]: ...


@dataclass
class Services:
    """Container for shared, request-scoped services held on ``app.state``."""

    config: AppConfig
    db: Database
    opensearch: ProjectionStore
    minio: BinaryStore
    queue: JobQueue
    search: SearchEngine
    events: EventRecorder | None = None
    timeline: TimelineService | None = None


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:  # pragma: no cover - defensive
        raise SagaError("Service container is not initialised.")
    return services


_bearer_scheme = HTTPBearer(auto_error=False, description="Bearer token (FR-35).")


def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> None:
    """Validate the Bearer token against the configured tokens."""
    services = get_services(request)
    token = credentials.credentials if credentials else None
    verify_bearer_token(token, services.config.security.tokens)


ServicesDep = Annotated[Services, Depends(get_services)]
AuthDep = Depends(require_auth)


def get_snapshot_service(request: Request) -> Any:  # noqa: ANN401 - SnapshotService
    snap = getattr(request.app.state, "snapshot", None)
    if snap is None:  # pragma: no cover - defensive
        raise SagaError("Snapshot service is not initialised.")
    return snap


SnapshotDep = Annotated[Any, Depends(get_snapshot_service)]
