"""Unit tests for the Ollama failover transports (httpx.MockTransport, no sockets)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from saga.ollama.config import OllamaServerConfig
from saga.ollama.pool import OllamaServerPool
from saga.ollama.transport import OllamaFailoverAsyncTransport, OllamaFailoverTransport

_A = "http://a:11434"
_B = "http://b:11434"


def _pool(*urls: str) -> OllamaServerPool:
    return OllamaServerPool([OllamaServerConfig(url=url) for url in urls])


def _client(pool: OllamaServerPool, handler: Any, **kwargs: Any) -> httpx.AsyncClient:
    transport = OllamaFailoverAsyncTransport(pool, inner=httpx.MockTransport(handler), **kwargs)
    return httpx.AsyncClient(transport=transport, base_url=_A)


async def test_failover_on_connect_error_rewrites_url_and_host_header() -> None:
    pool = _pool(_A, _B)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"]))
        if request.url.host == "a":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"ok": True})

    async with _client(pool, handler) as client:
        response = await client.post("/v1/chat/completions", json={"x": 1})

    assert response.status_code == 200
    hosts = [host for host, _ in seen]
    assert hosts.count("b") == 1
    # The Host header follows the rewritten URL.
    assert all(header.startswith(host) for host, header in seen)
    assert pool.snapshot()[_A]["healthy"] is False
    assert pool.snapshot()[_B]["healthy"] is True


async def test_failover_on_503_returns_body_from_second_server() -> None:
    pool = _pool(_A, _B)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a":
            return httpx.Response(503)
        return httpx.Response(200, json={"from": "b"})

    async with _client(pool, handler) as client:
        response = await client.post("/v1/chat/completions", json={})

    assert response.json() == {"from": "b"}
    assert pool.snapshot()[_A]["healthy"] is False


async def test_failover_on_404_model_missing() -> None:
    pool = _pool(_A, _B)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a":
            return httpx.Response(404)
        return httpx.Response(200, json={"from": "b"})

    async with _client(pool, handler) as client:
        response = await client.post("/v1/chat/completions", json={})

    assert response.status_code == 200


async def test_error_response_returned_when_no_alternative_left() -> None:
    pool = _pool(_A, _B)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _client(pool, handler) as client:
        response = await client.post("/v1/chat/completions", json={})

    assert response.status_code == 503
    assert pool.snapshot()[_A]["healthy"] is False
    assert pool.snapshot()[_B]["healthy"] is False


async def test_all_servers_down_raises_connect_error() -> None:
    pool = _pool(_A, _B)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(pool, handler) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post("/v1/chat/completions", json={})

    snapshot = pool.snapshot()
    assert snapshot[_A]["healthy"] is False
    assert snapshot[_B]["healthy"] is False
    assert snapshot[_A]["in_flight"] == 0
    assert snapshot[_B]["in_flight"] == 0


async def test_connect_timeout_clamped_but_read_timeout_kept() -> None:
    pool = _pool(_A)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.extensions.get("timeout", {}))
        return httpx.Response(200, json={})

    async with _client(pool, handler, connect_timeout=5.0) as client:
        await client.post("/v1/chat/completions", json={}, timeout=httpx.Timeout(300.0))

    assert captured["connect"] == 5.0
    assert captured["read"] == 300.0


class _ExplodingStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> Any:
        yield b"data: chunk1\n"
        raise httpx.ReadError("server died mid-stream")


async def test_mid_stream_failure_marks_server_dead_and_releases_slot() -> None:
    pool = _pool(_A)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ExplodingStream())

    async with _client(pool, handler) as client:
        with pytest.raises(httpx.ReadError):
            async with client.stream("POST", "/v1/chat/completions", json={}) as response:
                async for _ in response.aiter_bytes():
                    pass

    assert pool.snapshot()[_A]["healthy"] is False
    assert pool.snapshot()[_A]["in_flight"] == 0


async def test_busy_server_is_avoided_while_streaming() -> None:
    pool = _pool(_A, _B)
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={})

    async with (
        _client(pool, handler) as client,
        client.stream("POST", "/v1/chat/completions", json={}) as first,
    ):
        first_host = hosts[0]
        # While the first response is open its server stays loaded, so the
        # second request must go to the other server.
        await client.post("/v1/chat/completions", json={})
        await first.aread()

    assert len(hosts) == 2
    assert hosts[1] != first_host


async def test_sequential_requests_distribute_evenly() -> None:
    pool = _pool(_A, _B)
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={})

    async with _client(pool, handler) as client:
        for _ in range(4):
            await client.post("/v1/chat/completions", json={})

    assert hosts.count("a") == 2
    assert hosts.count("b") == 2


def test_sync_transport_failover_on_connect_error() -> None:
    pool = _pool(_A, _B)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"ok": True})

    transport = OllamaFailoverTransport(pool, inner=httpx.MockTransport(handler))
    with httpx.Client(transport=transport, base_url=_A) as client:
        response = client.post("/v1/chat/completions", json={})

    assert response.status_code == 200
    assert pool.snapshot()[_A]["healthy"] is False
    assert pool.snapshot()[_A]["in_flight"] == 0
    assert pool.snapshot()[_B]["in_flight"] == 0
