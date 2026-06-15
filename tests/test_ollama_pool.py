"""Unit tests for the least-busy Ollama server pool (no network)."""

from __future__ import annotations

import asyncio

import pytest

from saga.core.errors import ConfigError
from saga.ollama.config import OllamaServerConfig
from saga.ollama.pool import OllamaServerPool, get_pool


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _servers(*urls: str, max_concurrent: int | None = None) -> list[OllamaServerConfig]:
    return [OllamaServerConfig(url=url, max_concurrent=max_concurrent) for url in urls]


def _pool(*urls: str, **kwargs: object) -> tuple[OllamaServerPool, _Clock]:
    clock = _Clock()
    pool = OllamaServerPool(_servers(*urls), clock=clock, poll_interval=0.01, **kwargs)  # type: ignore[arg-type]
    return pool, clock


def test_empty_server_list_raises() -> None:
    with pytest.raises(ConfigError):
        OllamaServerPool([])


def test_least_busy_server_wins() -> None:
    pool, _ = _pool("http://a:1", "http://b:1")
    lease = pool.lease("http://a:1")
    assert pool.candidates()[0] == "http://b:1"
    lease.release()


def test_ties_rotate_between_equally_idle_servers() -> None:
    pool, _ = _pool("http://a:1", "http://b:1")
    first = pool.candidates()[0]
    second = pool.candidates()[0]
    assert {first, second} == {"http://a:1", "http://b:1"}
    assert first != second


def test_lease_release_is_idempotent() -> None:
    pool, _ = _pool("http://a:1")
    lease = pool.lease("http://a:1")
    lease.release()
    lease.release()
    assert pool.snapshot()["http://a:1"]["in_flight"] == 0


def test_server_at_max_concurrent_is_deprioritised() -> None:
    clock = _Clock()
    servers = [
        OllamaServerConfig(url="http://a:1", max_concurrent=1),
        OllamaServerConfig(url="http://b:1"),
    ]
    pool = OllamaServerPool(servers, clock=clock)
    lease_b = pool.lease("http://b:1")
    lease_b2 = pool.lease("http://b:1")  # b is busier than a ...
    lease_a = pool.lease("http://a:1")  # ... but a is now at its hard cap
    assert pool.candidates()[0] == "http://b:1"
    for lease in (lease_a, lease_b, lease_b2):
        lease.release()


async def test_acquire_waits_until_a_slot_frees() -> None:
    clock = _Clock()
    pool = OllamaServerPool(
        [OllamaServerConfig(url="http://a:1", max_concurrent=1)],
        clock=clock,
        poll_interval=0.01,
    )
    first = await pool.acquire()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(pool.acquire(), timeout=0.05)
    first.release()
    second = await asyncio.wait_for(pool.acquire(), timeout=1.0)
    assert second.url == "http://a:1"
    second.release()


def test_failed_server_moves_to_last_resort_and_recovers_after_cooldown() -> None:
    pool, clock = _pool("http://a:1", "http://b:1", cooldown_seconds=30.0)
    pool.mark_failure("http://a:1")
    assert pool.candidates() == ["http://b:1", "http://a:1"]
    assert pool.snapshot()["http://a:1"]["healthy"] is False
    clock.now = 31.0
    assert pool.snapshot()["http://a:1"]["healthy"] is True


def test_mark_success_heals_immediately() -> None:
    pool, _ = _pool("http://a:1", "http://b:1")
    pool.mark_failure("http://a:1")
    pool.mark_success("http://a:1")
    assert pool.snapshot()["http://a:1"]["healthy"] is True


def test_all_down_still_probes_every_server() -> None:
    pool, _ = _pool("http://a:1", "http://b:1")
    pool.mark_failure("http://a:1")
    pool.mark_failure("http://b:1")
    assert set(pool.candidates()) == {"http://a:1", "http://b:1"}


async def test_acquire_does_not_wait_when_all_servers_are_down() -> None:
    pool, _ = _pool("http://a:1", "http://b:1")
    pool.mark_failure("http://a:1")
    pool.mark_failure("http://b:1")
    lease = await asyncio.wait_for(pool.acquire(), timeout=1.0)
    assert lease.url in {"http://a:1", "http://b:1"}
    lease.release()


def test_get_pool_shares_instances_per_fleet() -> None:
    servers = _servers("http://share-a:1", "http://share-b:1")
    assert get_pool(servers) is get_pool(list(servers))
    assert get_pool(servers) is not get_pool(_servers("http://share-c:1"))
