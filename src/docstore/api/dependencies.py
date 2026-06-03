"""Dependency wiring for the REST API: the service container and auth (FR-35)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from docstore.api.auth import verify_bearer_token
from docstore.core.errors import DocStoreError

if TYPE_CHECKING:
    from docstore.core.config import AppConfig
    from docstore.core.models import Document


class DocumentStore(Protocol):
    """The document-index operations the API depends on (DIP for testability)."""

    async def find_by_hash(self, content_hash: str) -> Document | None: ...
    async def get_document(self, document_id: str) -> Document | None: ...
    async def index_document(self, document: Document) -> None: ...
    async def delete_document(self, document_id: str) -> None: ...
    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]: ...


class BinaryStore(Protocol):
    """The object-storage operations the API depends on."""

    async def put_object(self, object_name: str, data: bytes, content_type: str) -> str: ...
    async def get_object(self, object_name: str) -> bytes: ...
    async def remove_object(self, object_name: str) -> None: ...


class JobQueue(Protocol):
    """Minimal interface for enqueueing background jobs (ARQ ``ArqRedis``)."""

    async def enqueue_job(self, function: str, *args: Any) -> Any: ...  # noqa: ANN401


@dataclass
class Services:
    """Container for shared, request-scoped services held on ``app.state``."""

    config: AppConfig
    opensearch: DocumentStore
    minio: BinaryStore
    queue: JobQueue


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:  # pragma: no cover - defensive
        raise DocStoreError("Service container is not initialised.")
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
