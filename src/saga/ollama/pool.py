"""Least-busy server pool for a fleet of Ollama servers.

Tracks per-server load (in-flight requests) and health (dead-marking with a
cooldown). Selection always prefers the healthy server with the fewest running
requests, so a fast machine that finishes quickly automatically receives more
work than a slow one that is still busy — the fleet's throughput adds up
instead of a slow server queueing requests while a fast one idles. An optional
``max_concurrent`` per server is a hard cap against overloading weak machines.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Any

from saga.core.errors import ConfigError
from saga.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saga.ollama.config import OllamaServerConfig

_log = get_logger("saga.ollama.pool")


class OllamaLease:
    """A reserved slot on one server; ``release()`` frees it (idempotent)."""

    def __init__(self, pool: OllamaServerPool, url: str) -> None:
        self.url = url
        self._pool = pool
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._pool._release(self.url)

    def __enter__(self) -> OllamaLease:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


class _ServerState:
    __slots__ = ("dead_until", "in_flight", "max_concurrent", "order")

    def __init__(self, order: int, max_concurrent: int | None) -> None:
        self.order = order
        self.max_concurrent = max_concurrent
        self.in_flight = 0
        self.dead_until = 0.0


class OllamaServerPool:
    """Least-busy selection with failure cooldowns over a fixed server fleet."""

    def __init__(
        self,
        servers: Sequence[OllamaServerConfig],
        *,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.1,
    ) -> None:
        if not servers:
            raise ConfigError("OllamaServerPool requires at least one server.")
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._rotation = 0
        self._servers: dict[str, _ServerState] = {}
        for server in servers:
            if not server.url:
                raise ConfigError("OllamaServerPool received a server without a url.")
            url = server.url.rstrip("/")
            if url not in self._servers:
                self._servers[url] = _ServerState(len(self._servers), server.max_concurrent)

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(self._servers)

    def _healthy(self, state: _ServerState, now: float) -> bool:
        return now >= state.dead_until

    def _sort_key(self, url: str, state: _ServerState) -> tuple[int, int]:
        # Least in-flight first; ties rotate via the shared rotation counter so
        # equally idle servers share work instead of the first one taking all.
        n = len(self._servers)
        return (state.in_flight, (state.order - self._rotation) % n)

    def candidates(self) -> list[str]:
        """Server URLs in the order failover should try them.

        Healthy servers under their ``max_concurrent`` come first (least busy
        first), then healthy servers at capacity, then servers in cooldown as a
        last resort — an all-down fleet still probes every server, so a freshly
        rebooted machine is picked up before its cooldown expires.
        """
        with self._lock:
            now = self._clock()
            free: list[str] = []
            at_capacity: list[str] = []
            dead: list[str] = []
            for url, state in self._servers.items():
                if not self._healthy(state, now):
                    dead.append(url)
                elif state.max_concurrent is not None and state.in_flight >= state.max_concurrent:
                    at_capacity.append(url)
                else:
                    free.append(url)
            free.sort(key=lambda u: self._sort_key(u, self._servers[u]))
            at_capacity.sort(key=lambda u: self._sort_key(u, self._servers[u]))
            dead.sort(key=lambda u: self._servers[u].dead_until)
            self._rotation += 1
            return [*free, *at_capacity, *dead]

    def lease(self, url: str) -> OllamaLease:
        """Reserve a slot on ``url`` unconditionally (used by failover attempts)."""
        with self._lock:
            self._servers[url].in_flight += 1
        return OllamaLease(self, url)

    def _pick_locked(self) -> str | None:
        """Best server right now, or ``None`` when all healthy servers are at capacity."""
        now = self._clock()
        free = [
            url
            for url, state in self._servers.items()
            if self._healthy(state, now)
            and (state.max_concurrent is None or state.in_flight < state.max_concurrent)
        ]
        if free:
            best = min(free, key=lambda u: self._sort_key(u, self._servers[u]))
            self._rotation += 1
            return best
        if any(self._healthy(state, now) for state in self._servers.values()):
            return None  # healthy servers exist but all are at capacity -> wait
        # All servers in cooldown: probe the one whose cooldown expires soonest
        # instead of waiting — a rebooted machine answers, the rest fail fast.
        return min(self._servers, key=lambda u: self._servers[u].dead_until)

    async def acquire(self) -> OllamaLease:
        """Lease the least-busy server, waiting while every healthy server is full.

        Waiting (instead of overflowing past ``max_concurrent``) keeps the queue
        central: the next request goes to whichever server frees a slot first,
        which is by definition the fastest one available.
        """
        while True:
            with self._lock:
                url = self._pick_locked()
                if url is not None:
                    self._servers[url].in_flight += 1
                    return OllamaLease(self, url)
            await asyncio.sleep(self._poll_interval)

    def _release(self, url: str) -> None:
        with self._lock:
            state = self._servers[url]
            state.in_flight = max(state.in_flight - 1, 0)

    def mark_failure(self, url: str) -> None:
        with self._lock:
            state = self._servers[url]
            was_healthy = self._healthy(state, self._clock())
            state.dead_until = self._clock() + self._cooldown
        if was_healthy:
            _log.warning("ollama_server_down", url=url, cooldown_seconds=self._cooldown)

    def mark_success(self, url: str) -> None:
        with self._lock:
            state = self._servers[url]
            was_dead = not self._healthy(state, self._clock())
            state.dead_until = 0.0
        if was_dead:
            _log.info("ollama_server_recovered", url=url)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Per-server load/health view (used by the /health endpoint and logs)."""
        with self._lock:
            now = self._clock()
            return {
                url: {
                    "healthy": self._healthy(state, now),
                    "in_flight": state.in_flight,
                    "max_concurrent": state.max_concurrent,
                }
                for url, state in self._servers.items()
            }


_pools: dict[tuple[Any, ...], OllamaServerPool] = {}
_pools_lock = threading.Lock()


def get_pool(
    servers: Sequence[OllamaServerConfig], *, cooldown_seconds: float = 30.0
) -> OllamaServerPool:
    """Shared pool per fleet: every chat model and the embeddings adapter that
    use the same server list get the same pool, so load and health knowledge
    (e.g. "the gaming PC is off") is shared between all of them."""
    key = (tuple((s.url, s.max_concurrent) for s in servers), cooldown_seconds)
    with _pools_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = OllamaServerPool(servers, cooldown_seconds=cooldown_seconds)
            _pools[key] = pool
        return pool
