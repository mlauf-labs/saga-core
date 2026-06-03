"""Converter interface + shared HTTP base for Docling/Kreuzberg clients (FR-4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from docstore.core.errors import ConversionError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from docstore.converters.config import ServiceConfig


class Converter(Protocol):
    """Converts a binary document to Markdown text."""

    name: str

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        """Convert ``data`` to Markdown. Raises ``ConversionError`` on failure."""
        ...

    async def aclose(self) -> None:
        """Release any underlying network resources."""
        ...


# Transient HTTP failures worth retrying (NFR-14).
_RETRYABLE = (httpx.TransportError, httpx.RemoteProtocolError)


class HttpConverter:
    """Base class providing a pooled ``httpx.AsyncClient`` with retries."""

    name: str = "http"

    def __init__(self, config: ServiceConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout_seconds,
            )
        return self._client

    async def _post_with_retry(
        self, request: Callable[[], Awaitable[httpx.Response]]
    ) -> httpx.Response:
        """Execute ``request`` with exponential-backoff retries on transient errors."""
        retrying = retry(
            reraise=True,
            stop=stop_after_attempt(max(self._config.max_retries, 1)),
            wait=wait_exponential(multiplier=0.5, max=8),
            retry=retry_if_exception_type(_RETRYABLE),
        )
        try:
            return await retrying(request)()
        except _RETRYABLE as exc:
            raise ConversionError(
                f"{self.name}: conversion service at '{self._config.base_url}' is "
                f"unreachable after {self._config.max_retries} attempt(s): {exc}"
            ) from exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
