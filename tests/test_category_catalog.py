"""Unit tests for the cached category catalog (FR-16)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from docstore.search.catalog import CategoryCatalog


def _source(terms: list[tuple[str, int]]) -> MagicMock:
    src = MagicMock()
    src.category_terms = AsyncMock(return_value=terms)
    return src


async def test_get_paths_returns_sorted_unique() -> None:
    src = _source([("Finance/Invoices", 3), ("Insurance", 1), ("Finance/Invoices", 2), ("", 5)])
    catalog = CategoryCatalog(src)
    assert await catalog.get_paths() == ["Finance/Invoices", "Insurance"]


async def test_get_paths_is_cached_within_ttl() -> None:
    src = _source([("Finance", 1)])
    catalog = CategoryCatalog(src, ttl_seconds=300.0)
    await catalog.get_paths()
    await catalog.get_paths()
    src.category_terms.assert_awaited_once()


async def test_invalidate_forces_refetch() -> None:
    src = _source([("Finance", 1)])
    catalog = CategoryCatalog(src, ttl_seconds=300.0)
    await catalog.get_paths()
    catalog.invalidate()
    await catalog.get_paths()
    assert src.category_terms.await_count == 2


async def test_ttl_expiry_forces_refetch() -> None:
    src = _source([("Finance", 1)])
    catalog = CategoryCatalog(src, ttl_seconds=0.0)  # always stale
    await catalog.get_paths()
    await catalog.get_paths()
    assert src.category_terms.await_count == 2


async def test_as_write_listener_returns_invalidate() -> None:
    catalog = CategoryCatalog(_source([("Finance", 1)]))
    await catalog.get_paths()
    catalog.as_write_listener()()  # invoke the returned callback
    await catalog.get_paths()
    # Two fetches: one before invalidation, one after.
    assert catalog._source.category_terms.await_count == 2  # type: ignore[attr-defined]
