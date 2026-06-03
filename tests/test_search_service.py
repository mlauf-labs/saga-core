"""Unit tests for the search service (query embedding + delegation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from docstore.core.models import SearchHit
from docstore.search.service import SearchService


@pytest.fixture
def opensearch() -> MagicMock:
    store = MagicMock()
    store.hybrid_search = AsyncMock(
        return_value=[
            SearchHit(document_id="d1", chunk_id="d1:0", snippet="s", score=1.0, title="t")
        ]
    )
    store.category_terms = AsyncMock(return_value=[("Finance/Invoices", 2)])
    store.list_documents_in_category = AsyncMock(return_value=([], 0))
    store.get_document = AsyncMock(return_value=None)
    return store


@pytest.fixture
def embedder() -> MagicMock:
    fake = MagicMock()
    fake.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    return fake


def _service(opensearch: MagicMock, embedder: MagicMock) -> SearchService:
    return SearchService(opensearch=opensearch, embedder=embedder, default_top_k=10, max_top_k=20)


async def test_hybrid_search_embeds_query(opensearch: MagicMock, embedder: MagicMock) -> None:
    service = _service(opensearch, embedder)
    hits = await service.hybrid_search(query="hello", doc_type="invoice")
    assert len(hits) == 1
    embedder.embed.assert_awaited_once_with(["hello"])
    call = opensearch.hybrid_search.await_args.kwargs
    assert call["query_vector"] == [0.1, 0.2, 0.3]
    assert call["top_k"] == 10
    assert call["filters"] is not None


async def test_top_k_is_bounded(opensearch: MagicMock, embedder: MagicMock) -> None:
    service = _service(opensearch, embedder)
    await service.hybrid_search(query="x", top_k=999)
    assert opensearch.hybrid_search.await_args.kwargs["top_k"] == 20


async def test_top_k_default_for_invalid(opensearch: MagicMock, embedder: MagicMock) -> None:
    service = _service(opensearch, embedder)
    await service.hybrid_search(query="x", top_k=0)
    assert opensearch.hybrid_search.await_args.kwargs["top_k"] == 10


async def test_no_filters_passes_none(opensearch: MagicMock, embedder: MagicMock) -> None:
    service = _service(opensearch, embedder)
    await service.hybrid_search(query="x")
    assert opensearch.hybrid_search.await_args.kwargs["filters"] is None


async def test_get_category_tree(opensearch: MagicMock, embedder: MagicMock) -> None:
    service = _service(opensearch, embedder)
    tree = await service.get_category_tree()
    assert tree[0].name == "Finance"
    assert tree[0].children[0].name == "Invoices"
