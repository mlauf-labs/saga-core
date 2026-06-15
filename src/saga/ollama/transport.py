"""httpx transports that distribute requests across an Ollama server fleet.

The LangChain ``ChatOpenAI`` client for Ollama keeps a single ``base_url``; these
transports re-route every request to the least-busy healthy server of an
:class:`~saga.ollama.pool.OllamaServerPool` and fail over to the next server
on connection errors and on 404/502/503/504 responses (404 = model not pulled on
that server yet). Working at the transport level keeps ``bind_tools``, SSE
streaming, and the structured-output retry behaviour completely untouched.

Two things the transports must handle themselves:

* The openai SDK injects its own per-request timeout (e.g. 300 s) via the
  ``timeout`` request extension, overriding whatever the ``httpx`` client was
  configured with. The *connect* timeout is clamped here so failover from a
  powered-off host takes seconds, not minutes; read/write timeouts stay intact
  (streaming's per-chunk semantics are preserved).
* A server dying mid-stream cannot be retried transparently (data was already
  delivered). The response stream is wrapped so the failure marks the server
  dead and releases its load slot; the structured-output library retries the
  call and the next attempt lands on a healthy server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

try:  # Replayability check for request bodies; private but stable for years.
    from httpx._content import ByteStream as _ByteStream
except ImportError:  # pragma: no cover - future httpx refactor
    _ByteStream = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from saga.ollama.pool import OllamaLease, OllamaServerPool

#: Response statuses that trigger failover to the next server. 404 covers a
#: model that is missing (or still being pulled) on one server; 502/503/504
#: cover reverse proxies in front of a down Ollama.
_FAILOVER_STATUS = frozenset({404, 502, 503, 504})

_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


def _attach_lease(
    response: httpx.Response,
    pool: OllamaServerPool,
    url: str,
    lease: OllamaLease,
    *,
    sync: bool,
) -> httpx.Response:
    """Tie the server's load slot to the response lifetime.

    A response whose body is already buffered (e.g. test transports) means the
    server's work is done — release immediately. Live streams keep the slot
    until the stream is closed/exhausted, because the GPU is busy for the whole
    generation, not just the connection setup.
    """
    if response.is_stream_consumed or response.is_closed:
        lease.release()
        return response
    if sync:
        response.stream = _ObservingSyncStream(response.stream, pool, url, lease)  # type: ignore[arg-type]
    else:
        response.stream = _ObservingAsyncStream(response.stream, pool, url, lease)  # type: ignore[arg-type]
    return response


def _is_replayable(request: httpx.Request) -> bool:
    """Whether the request body can be re-sent to another server."""
    return _ByteStream is not None and isinstance(request.stream, _ByteStream)


def _clamped_extensions(request: httpx.Request, connect_timeout: float) -> dict[str, Any]:
    extensions = dict(request.extensions)
    timeout = dict(extensions.get("timeout") or {})
    current = timeout.get("connect")
    if current is None or current > connect_timeout:
        timeout["connect"] = connect_timeout
    extensions["timeout"] = timeout
    return extensions


def _reroute(
    request: httpx.Request, server_url: str, extensions: dict[str, Any]
) -> httpx.Request:
    """Copy of ``request`` pointed at ``server_url`` (path/query untouched)."""
    target = httpx.URL(server_url)
    new_url = request.url.copy_with(scheme=target.scheme, host=target.host, port=target.port)
    headers = request.headers.copy()
    # httpx bakes the Host header in at client level, not transport level.
    headers["Host"] = new_url.netloc.decode("ascii")
    return httpx.Request(
        request.method, new_url, headers=headers, stream=request.stream, extensions=extensions
    )


class _ObservingAsyncStream(httpx.AsyncByteStream):
    """Holds the server's load slot for the whole response (the GPU is busy
    until generation finishes, not just during connection setup) and marks the
    server dead when the stream breaks mid-generation."""

    def __init__(
        self,
        inner: httpx.AsyncByteStream,
        pool: OllamaServerPool,
        url: str,
        lease: OllamaLease,
    ) -> None:
        self._inner = inner
        self._pool = pool
        self._url = url
        self._lease = lease

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._inner:
                yield chunk
        except Exception:
            self._pool.mark_failure(self._url)
            self._lease.release()
            raise

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            self._lease.release()


class _ObservingSyncStream(httpx.SyncByteStream):
    def __init__(
        self,
        inner: httpx.SyncByteStream,
        pool: OllamaServerPool,
        url: str,
        lease: OllamaLease,
    ) -> None:
        self._inner = inner
        self._pool = pool
        self._url = url
        self._lease = lease

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._inner
        except Exception:
            self._pool.mark_failure(self._url)
            self._lease.release()
            raise

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._lease.release()


class OllamaFailoverAsyncTransport(httpx.AsyncBaseTransport):
    """Async transport: least-busy routing + failover over the server pool."""

    def __init__(
        self,
        pool: OllamaServerPool,
        *,
        connect_timeout: float = 5.0,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._pool = pool
        self._connect_timeout = connect_timeout
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        extensions = _clamped_extensions(request, self._connect_timeout)
        replayable = _is_replayable(request)
        tried: set[str] = set()
        lease = await self._pool.acquire()
        while True:
            url = lease.url
            tried.add(url)
            try:
                response = await self._inner.handle_async_request(
                    _reroute(request, url, extensions)
                )
            except _CONNECT_ERRORS:
                lease.release()
                self._pool.mark_failure(url)
                next_url = self._next_candidate(tried) if replayable else None
                if next_url is None:
                    raise
                lease = self._pool.lease(next_url)
                continue
            if response.status_code in _FAILOVER_STATUS:
                self._pool.mark_failure(url)
                next_url = self._next_candidate(tried) if replayable else None
                if next_url is None:
                    # No alternative left: hand the error response to the caller.
                    return _attach_lease(response, self._pool, url, lease, sync=False)
                await response.aclose()
                lease.release()
                lease = self._pool.lease(next_url)
                continue
            self._pool.mark_success(url)
            return _attach_lease(response, self._pool, url, lease, sync=False)

    def _next_candidate(self, tried: set[str]) -> str | None:
        return next((u for u in self._pool.candidates() if u not in tried), None)

    async def aclose(self) -> None:
        await self._inner.aclose()


class OllamaFailoverTransport(httpx.BaseTransport):
    """Sync mirror of :class:`OllamaFailoverAsyncTransport`.

    Practically unused (the whole pipeline is async) but ``ChatOpenAI`` keeps a
    sync client too. No waiting on ``max_concurrent`` here: selection simply
    takes the best candidate (soft limit).
    """

    def __init__(
        self,
        pool: OllamaServerPool,
        *,
        connect_timeout: float = 5.0,
        inner: httpx.BaseTransport | None = None,
    ) -> None:
        self._pool = pool
        self._connect_timeout = connect_timeout
        self._inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        extensions = _clamped_extensions(request, self._connect_timeout)
        replayable = _is_replayable(request)
        tried: set[str] = set()
        candidates = self._pool.candidates()
        if not candidates:  # pragma: no cover - pool guarantees >= 1 server
            raise httpx.ConnectError("No Ollama servers configured.")
        lease = self._pool.lease(candidates[0])
        while True:
            url = lease.url
            tried.add(url)
            try:
                response = self._inner.handle_request(_reroute(request, url, extensions))
            except _CONNECT_ERRORS:
                lease.release()
                self._pool.mark_failure(url)
                next_url = self._next_candidate(tried) if replayable else None
                if next_url is None:
                    raise
                lease = self._pool.lease(next_url)
                continue
            if response.status_code in _FAILOVER_STATUS:
                self._pool.mark_failure(url)
                next_url = self._next_candidate(tried) if replayable else None
                if next_url is None:
                    return _attach_lease(response, self._pool, url, lease, sync=True)
                response.close()
                lease.release()
                lease = self._pool.lease(next_url)
                continue
            self._pool.mark_success(url)
            return _attach_lease(response, self._pool, url, lease, sync=True)

    def _next_candidate(self, tried: set[str]) -> str | None:
        return next((u for u in self._pool.candidates() if u not in tried), None)

    def close(self) -> None:
        self._inner.close()
