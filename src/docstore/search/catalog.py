"""In-memory cache of the existing category paths (FR-16).

The category tree is derived from the ``category_paths`` of all documents via a single
aggregation query. That result is cached for a configurable TTL and reused (e.g. as
context for the LLM categorisation step). Database writes that can change the set of
categories invalidate the cache immediately, so the next read re-queries the database
and the catalogue is always current.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Protocol

from docstore.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

_log = get_logger("docstore.search.catalog")


class CategoryTermsSource(Protocol):
    """Provides the existing category paths with document counts."""

    async def category_terms(self) -> list[tuple[str, int]]: ...


class CategoryCatalog:
    """Caches the sorted, unique list of existing category paths with a TTL."""

    def __init__(self, source: CategoryTermsSource, *, ttl_seconds: float = 300.0) -> None:
        self._source = source
        self._ttl = ttl_seconds
        self._paths: list[str] | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    def _is_fresh(self) -> bool:
        return self._paths is not None and (time.monotonic() - self._fetched_at) < self._ttl

    async def get_paths(self) -> list[str]:
        """Return the cached category paths, re-querying when stale or invalidated."""
        if self._is_fresh():
            return self._paths or []
        async with self._lock:
            # Re-check after acquiring the lock (another task may have refreshed).
            if self._is_fresh():
                return self._paths or []
            terms = await self._source.category_terms()
            paths = sorted({path for path, _ in terms if path.strip()})
            self._paths = paths
            self._fetched_at = time.monotonic()
            _log.debug("category_catalog_refreshed", count=len(paths))
            return paths

    def invalidate(self) -> None:
        """Drop the cache so the next ``get_paths`` re-queries the database."""
        self._paths = None

    def as_write_listener(self) -> Callable[[], None]:
        """Return a callback suitable for registering as a store write listener."""
        return self.invalidate
