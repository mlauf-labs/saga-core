"""Exception handling for the REST API: maps domain errors to HTTP responses with
actionable messages (NFR-15)."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from docstore.api.schemas import ErrorResponse
from docstore.core.errors import (
    AuthError,
    ConflictError,
    ConversionError,
    DocStoreError,
    NotFoundError,
    ProviderError,
    StorageError,
    ValidationError,
)
from docstore.core.logging import get_logger

_log = get_logger("docstore.api")

_STATUS_MAP: dict[type[DocStoreError], int] = {
    AuthError: status.HTTP_401_UNAUTHORIZED,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    ValidationError: status.HTTP_400_BAD_REQUEST,
    ConversionError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StorageError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ProviderError: status.HTTP_502_BAD_GATEWAY,
}


def _status_for(exc: DocStoreError) -> int:
    for error_type, http_status in _STATUS_MAP.items():
        if isinstance(exc, error_type):
            return http_status
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def register_exception_handlers(app: FastAPI) -> None:
    """Register handlers that turn ``DocStoreError`` into JSON error responses."""

    async def handle_docstore_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, DocStoreError)
        http_status = _status_for(exc)
        if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            _log.error("request_failed", code=exc.code, error=str(exc))
        else:
            _log.warning("request_rejected", code=exc.code, error=str(exc))
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthError) else None
        return JSONResponse(
            status_code=http_status,
            content=ErrorResponse(code=exc.code, message=str(exc)).model_dump(),
            headers=headers,
        )

    app.add_exception_handler(DocStoreError, handle_docstore_error)
