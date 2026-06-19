"""Unit tests for the OpenSearch search-projection store using a fake async client.

OpenSearch is now a derived, rebuildable projection (no longer the system of record),
so the store only exposes bootstrap/projection/search operations. The fake client
records every request and returns canned responses so the store's request building and
response parsing can be exercised without a cluster.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from saga.core.config import OpenSearchConfig
from saga.core.errors import StorageError
from saga.core.models import (
    Chunk,
    Document,
    DocumentStatus,
    ExtractedValue,
    FolderRef,
)
from saga.storage import opensearch as os_module
from saga.storage.opensearch import OpenSearchStore, _parse_hosts


def _make_document() -> Document:
    now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    return Document(
        document_id="d1",
        title="Invoice.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        content_hash="h",
        minio_object="b/d1",
        content_markdown="# Invoice",
        doc_type="invoice",
        summary="An invoice for 2024.",
        extracted_values=[
            ExtractedValue(key="amount", type="amount", value="100,00", normalized="100.00")
        ],
        folders=[
            FolderRef(folder_id="f-finance", name="Finance", is_primary=True),
            FolderRef(folder_id="f-invoices", name="Invoices"),
        ],
        status=DocumentStatus.READY,
        created_at=now,
        updated_at=now,
    )


def _compatible_mapping(index: str, **_: Any) -> dict[str, Any]:
    """Return a mapping where the required kNN (and nested) fields are correctly typed."""
    if "chunk" in index:
        return {index: {"mappings": {"properties": {"embedding": {"type": "knn_vector"}}}}}
    return {
        index: {
            "mappings": {
                "properties": {
                    "summary_embedding": {"type": "knn_vector"},
                    "metadata": {"type": "nested"},
                    "extracted_values": {"type": "nested"},
                }
            }
        }
    }


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.index = AsyncMock()
    client.delete = AsyncMock()
    client.delete_by_query = AsyncMock()
    client.search = AsyncMock()
    client.close = AsyncMock()
    client.indices = MagicMock()
    client.indices.exists = AsyncMock(return_value=False)
    client.indices.create = AsyncMock()
    client.indices.delete = AsyncMock()
    client.indices.get_mapping = AsyncMock(side_effect=_compatible_mapping)
    return client


@pytest.fixture
def store(fake_client: MagicMock) -> OpenSearchStore:
    return OpenSearchStore(OpenSearchConfig(), client=fake_client)


# --------------------------------------------------------------------------- #
# Host parsing & lazy client                                                    #
# --------------------------------------------------------------------------- #


def test_parse_hosts_variants() -> None:
    hosts = _parse_hosts("http://opensearch:9200, https://node2:9201")
    assert hosts[0] == {"host": "opensearch", "port": 9200, "use_ssl": False}
    assert hosts[1] == {"host": "node2", "port": 9201, "use_ssl": True}


def test_parse_hosts_defaults_scheme_and_port() -> None:
    hosts = _parse_hosts("opensearch")
    assert hosts[0] == {"host": "opensearch", "port": 9200, "use_ssl": False}


def test_parse_hosts_empty_raises() -> None:
    with pytest.raises(StorageError):
        _parse_hosts("  ,  ")


def test_client_lazy_build() -> None:
    cfg = OpenSearchConfig(hosts="http://localhost:9200", password="")
    store = OpenSearchStore(cfg)
    client: Any = store.client
    assert client is not None


# --------------------------------------------------------------------------- #
# bootstrap                                                                     #
# --------------------------------------------------------------------------- #


async def test_bootstrap_creates_both_indices(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    await store.bootstrap()
    assert fake_client.indices.create.await_count == 2
    created = {call.kwargs["index"] for call in fake_client.indices.create.await_args_list}
    assert created == {store._config.document_index, store._config.chunk_index}


async def test_bootstrap_skips_existing_compatible_indices(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.indices.exists = AsyncMock(return_value=True)
    await store.bootstrap()
    fake_client.indices.create.assert_not_awaited()
    fake_client.indices.delete.assert_not_awaited()


async def test_bootstrap_recreates_index_missing_knn_field(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    # The documents index exists but has no knn_vector summary_embedding (stale mapping).
    fake_client.indices.exists = AsyncMock(return_value=True)

    def _mapping(index: str, **_: Any) -> dict[str, Any]:
        if index == store._config.document_index:
            return {index: {"mappings": {"properties": {"summary_embedding": {"type": "float"}}}}}
        return _compatible_mapping(index)

    fake_client.indices.get_mapping = AsyncMock(side_effect=_mapping)
    await store.bootstrap()
    # The incompatible documents index is dropped and recreated; the chunk index is kept.
    fake_client.indices.delete.assert_awaited_once()
    assert fake_client.indices.delete.await_args.kwargs["index"] == store._config.document_index
    fake_client.indices.create.assert_awaited_once()
    assert fake_client.indices.create.await_args.kwargs["index"] == store._config.document_index


async def test_bootstrap_recreates_index_with_non_nested_metadata(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    # The documents index exists with a correct kNN field but a STALE metadata mapping
    # (metadata absent / not nested) — the real test-server drift that broke keyword search.
    fake_client.indices.exists = AsyncMock(return_value=True)

    def _mapping(index: str, **_: Any) -> dict[str, Any]:
        if index == store._config.document_index:
            return {
                index: {
                    "mappings": {
                        "properties": {
                            "summary_embedding": {"type": "knn_vector"},
                            "extracted_values": {"type": "nested"},
                            # metadata absent → must trigger a recreate
                        }
                    }
                }
            }
        return _compatible_mapping(index)

    fake_client.indices.get_mapping = AsyncMock(side_effect=_mapping)
    await store.bootstrap()
    fake_client.indices.delete.assert_awaited_once()
    assert fake_client.indices.delete.await_args.kwargs["index"] == store._config.document_index
    fake_client.indices.create.assert_awaited_once()
    assert fake_client.indices.create.await_args.kwargs["index"] == store._config.document_index


async def test_bootstrap_wraps_errors(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.indices.exists.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.bootstrap()


# --------------------------------------------------------------------------- #
# project_document                                                              #
# --------------------------------------------------------------------------- #


async def test_project_document_indexes_expected_body(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    document = _make_document()
    await store.project_document(
        document,
        folder_ancestor_ids=["f-finance", "f-invoices", "f-root"],
        summary_embedding=[0.1, 0.2, 0.3],
    )
    fake_client.index.assert_awaited_once()
    kwargs = fake_client.index.await_args.kwargs
    assert kwargs["index"] == store._config.document_index
    assert kwargs["id"] == "d1"
    assert kwargs["refresh"] is True
    body = kwargs["body"]
    assert body["document_id"] == "d1"
    assert body["summary"] == "An invoice for 2024."
    assert body["folder_ids"] == ["f-finance", "f-invoices"]
    assert body["folder_ancestor_ids"] == ["f-finance", "f-invoices", "f-root"]
    assert body["primary_folder_id"] == "f-finance"
    assert body["summary_embedding"] == [0.1, 0.2, 0.3]
    assert body["status"] == "ready"
    # value_terms are denormalised from the extracted values (raw + normalized).
    assert "amount=100,00" in body["value_terms"]
    assert "amount=100.00" in body["value_terms"]


async def test_project_document_omits_embedding_when_absent(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    await store.project_document(_make_document(), folder_ancestor_ids=["f-finance"])
    body = fake_client.index.await_args.kwargs["body"]
    assert "summary_embedding" not in body


async def test_project_document_wraps_errors(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.index.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.project_document(_make_document(), folder_ancestor_ids=[])


# --------------------------------------------------------------------------- #
# delete_document                                                               #
# --------------------------------------------------------------------------- #


async def test_delete_document_cascades_to_chunks(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    await store.delete_document("d1")
    fake_client.delete_by_query.assert_awaited_once()
    dbq = fake_client.delete_by_query.await_args.kwargs
    assert dbq["index"] == store._config.chunk_index
    assert dbq["body"]["query"]["term"]["document_id"] == "d1"
    fake_client.delete.assert_awaited_once()
    delete_kwargs = fake_client.delete.await_args.kwargs
    assert delete_kwargs["index"] == store._config.document_index
    assert delete_kwargs["id"] == "d1"


async def test_delete_document_wraps_errors(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.delete_by_query.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.delete_document("d1")


# --------------------------------------------------------------------------- #
# index_chunks / delete_chunks                                                  #
# --------------------------------------------------------------------------- #


async def test_index_chunks(store: OpenSearchStore, monkeypatch: pytest.MonkeyPatch) -> None:
    bulk = AsyncMock(return_value=(2, []))
    monkeypatch.setattr(os_module, "async_bulk", bulk)
    chunks = [
        Chunk(chunk_id="d1:0", document_id="d1", ordinal=0, snippet="a", embedding=[0.1]),
        Chunk(chunk_id="d1:1", document_id="d1", ordinal=1, snippet="b", embedding=[0.2]),
    ]
    assert await store.index_chunks(chunks) == 2
    bulk.assert_awaited_once()


async def test_index_chunks_empty(store: OpenSearchStore) -> None:
    assert await store.index_chunks([]) == 0


async def test_delete_chunks(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.delete_chunks("d1")
    fake_client.delete_by_query.assert_awaited_once()
    body = fake_client.delete_by_query.await_args.kwargs["body"]
    assert body["query"]["term"]["document_id"] == "d1"


# --------------------------------------------------------------------------- #
# keyword_search                                                                #
# --------------------------------------------------------------------------- #


async def test_keyword_search_parses_document_hits(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "d1",
                    "_score": 3.2,
                    "_source": {
                        "document_id": "d1",
                        "title": "Invoice.pdf",
                        "doc_type": "invoice",
                        "folder_ids": ["f-finance"],
                    },
                    "highlight": {"content_markdown": ["... matched <em>text</em> ..."]},
                }
            ]
        }
    }
    hits = await store.keyword_search(
        query="invoice AND 2024",
        fields=["title^3", "content_markdown"],
        default_operator="OR",
        top_k=5,
        filters=[{"term": {"doc_type": "invoice"}}],
    )
    assert len(hits) == 1
    hit = hits[0]
    assert hit.document_id == "d1"
    assert hit.score == 3.2
    assert hit.doc_type == "invoice"
    assert hit.folder_ids == ["f-finance"]
    assert hit.snippet == "... matched <em>text</em> ..."
    # Query targets the document index with a query_string clause and filters.
    assert fake_client.search.await_args.kwargs["index"] == store._config.document_index
    body = fake_client.search.await_args.kwargs["body"]
    assert body["query"]["bool"]["must"][0]["query_string"]["query"] == "invoice AND 2024"
    assert {"term": {"doc_type": "invoice"}} in body["query"]["bool"]["filter"]


async def test_keyword_search_without_highlight_has_no_snippet(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {"hits": [{"_id": "d1", "_score": 1.0, "_source": {"document_id": "d1"}}]}
    }
    hits = await store.keyword_search(query="x", fields=["title"], default_operator="OR", top_k=5)
    assert hits[0].snippet is None


async def test_keyword_search_wraps_errors(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.keyword_search(query="x", fields=["title"], default_operator="OR", top_k=5)


# --------------------------------------------------------------------------- #
# semantic_search                                                               #
# --------------------------------------------------------------------------- #


async def test_semantic_search_parses_hits(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "d1:0",
                    "_score": 0.9,
                    "_source": {
                        "document_id": "d1",
                        "chunk_id": "d1:0",
                        "snippet": "hello",
                        "title": "Invoice.pdf",
                        "doc_type": "invoice",
                        "folder_ids": ["f-finance"],
                    },
                }
            ]
        }
    }
    hits = await store.semantic_search(query_vector=[0.1, 0.2], top_k=5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.chunk_id == "d1:0"
    assert hit.document_id == "d1"
    assert hit.snippet == "hello"
    assert hit.score == 0.9
    assert hit.folder_ids == ["f-finance"]
    # Pure kNN query against the chunk index.
    assert fake_client.search.await_args.kwargs["index"] == store._config.chunk_index
    body = fake_client.search.await_args.kwargs["body"]
    assert body["query"]["knn"]["embedding"]["vector"] == [0.1, 0.2]


async def test_semantic_search_passes_filters(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {"hits": {"hits": []}}
    await store.semantic_search(
        query_vector=[0.1], top_k=3, filters=[{"term": {"doc_type": "invoice"}}]
    )
    knn = fake_client.search.await_args.kwargs["body"]["query"]["knn"]["embedding"]
    assert {"term": {"doc_type": "invoice"}} in knn["filter"]["bool"]["filter"]


async def test_semantic_search_wraps_errors(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.semantic_search(query_vector=[0.1], top_k=5)


# --------------------------------------------------------------------------- #
# document_search                                                               #
# --------------------------------------------------------------------------- #


async def test_document_search_returns_ids_and_total_dict(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {
            "total": {"value": 5},
            "hits": [
                {"_id": "d1", "_source": {"document_id": "d1"}},
                {"_id": "d2", "_source": {"document_id": "d2"}},
            ],
        }
    }
    ids, total = await store.document_search(
        query="invoice", filters=[{"term": {"doc_type": "invoice"}}], from_=0, size=10
    )
    assert ids == ["d1", "d2"]
    assert total == 5
    assert fake_client.search.await_args.kwargs["index"] == store._config.document_index


async def test_document_search_total_as_int(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {"hits": {"total": 2, "hits": []}}
    ids, total = await store.document_search(query=None, filters=[], from_=0, size=10)
    assert ids == []
    assert total == 2


async def test_document_search_falls_back_to_hit_id(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {"total": {"value": 1}, "hits": [{"_id": "d9", "_source": {}}]}
    }
    ids, _ = await store.document_search(query=None, filters=[], from_=0, size=10)
    assert ids == ["d9"]


async def test_document_search_wraps_errors(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.document_search(query=None, filters=[], from_=0, size=10)


# --------------------------------------------------------------------------- #
# similar_by_summary (kNN over summary embedding)                               #
# --------------------------------------------------------------------------- #


async def test_similar_by_summary_parses_similar_documents(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "d2",
                    "_score": 0.8,
                    "_source": {
                        "document_id": "d2",
                        "title": "Other invoice",
                        "doc_type": "invoice",
                        "summary": "Another invoice.",
                        "folder_ids": ["f-finance"],
                        "primary_folder_id": "f-finance",
                        "value_terms": ["amount=50.00"],
                    },
                }
            ]
        }
    }
    results = await store.similar_by_summary(
        query_vector=[0.1, 0.2], top_k=5, exclude_document_id="d1"
    )
    assert len(results) == 1
    sim = results[0]
    assert sim.document_id == "d2"
    assert sim.score == 0.8
    assert sim.summary == "Another invoice."
    assert sim.primary_folder_id == "f-finance"
    assert sim.value_terms == ["amount=50.00"]
    # kNN query against the document index, excluding the source document.
    assert fake_client.search.await_args.kwargs["index"] == store._config.document_index
    knn = fake_client.search.await_args.kwargs["body"]["query"]["knn"]["summary_embedding"]
    assert knn["vector"] == [0.1, 0.2]
    must_not = knn["filter"]["bool"]["must_not"]
    assert {"term": {"document_id": "d1"}} in must_not


async def test_similar_by_summary_wraps_errors(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.similar_by_summary(query_vector=[0.1], top_k=5)


# --------------------------------------------------------------------------- #
# similar_by_text (more_like_this)                                              #
# --------------------------------------------------------------------------- #


async def test_similar_by_text_parses_similar_documents(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "d3",
                    "_score": 4.2,
                    "_source": {
                        "document_id": "d3",
                        "title": "Similar contract",
                        "doc_type": "contract",
                        "summary": "A contract.",
                        "folder_ids": ["f-legal"],
                        "primary_folder_id": "f-legal",
                        "value_terms": [],
                    },
                }
            ]
        }
    }
    results = await store.similar_by_text(text="liability", top_k=5, exclude_document_id="d1")
    assert len(results) == 1
    sim = results[0]
    assert sim.document_id == "d3"
    assert sim.score == 4.2
    assert sim.doc_type == "contract"
    assert sim.folder_ids == ["f-legal"]
    # more_like_this query against the document index, excluding the source document.
    assert fake_client.search.await_args.kwargs["index"] == store._config.document_index
    body = fake_client.search.await_args.kwargs["body"]
    mlt = body["query"]["bool"]["must"][0]["more_like_this"]
    assert mlt["like"] == "liability"
    assert {"term": {"document_id": "d1"}} in body["query"]["bool"]["must_not"]


async def test_similar_by_text_wraps_errors(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.similar_by_text(text="x", top_k=5)


# --------------------------------------------------------------------------- #
# close                                                                         #
# --------------------------------------------------------------------------- #


async def test_close(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.close()
    fake_client.close.assert_awaited_once()
