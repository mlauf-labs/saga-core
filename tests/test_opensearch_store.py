"""Unit tests for the OpenSearch store using a mocked async client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from docstore.core.config import OpenSearchConfig
from docstore.core.errors import NotFoundError, StorageError
from docstore.core.models import Chunk, Document, DocumentStatus
from docstore.storage import opensearch as os_module
from docstore.storage.opensearch import OpenSearchStore, _parse_hosts


def _make_document() -> Document:
    now = datetime.now(UTC)
    return Document(
        document_id="d1",
        title="Invoice.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        content_hash="h",
        minio_object="b/d1",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.index = AsyncMock()
    client.get = AsyncMock()
    client.update = AsyncMock()
    client.delete = AsyncMock()
    client.delete_by_query = AsyncMock()
    client.search = AsyncMock()
    client.close = AsyncMock()
    client.update_by_query = AsyncMock()
    client.indices = MagicMock()
    client.indices.exists = AsyncMock(return_value=False)
    client.indices.create = AsyncMock()
    client.indices.put_mapping = AsyncMock()
    client.transport = MagicMock()
    client.transport.perform_request = AsyncMock()
    return client


@pytest.fixture
def store(fake_client: MagicMock) -> OpenSearchStore:
    return OpenSearchStore(OpenSearchConfig(), client=fake_client)


def test_parse_hosts_variants() -> None:
    hosts = _parse_hosts("http://opensearch:9200, https://node2:9201")
    assert hosts[0] == {"host": "opensearch", "port": 9200, "use_ssl": False}
    assert hosts[1]["use_ssl"] is True


def test_parse_hosts_empty_raises() -> None:
    with pytest.raises(StorageError):
        _parse_hosts("  ,  ")


async def test_bootstrap_creates_indices_and_pipeline(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    await store.bootstrap()
    assert fake_client.indices.create.await_count == 2
    fake_client.transport.perform_request.assert_awaited_once()


async def test_index_document(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.index_document(_make_document())
    fake_client.index.assert_awaited_once()
    assert fake_client.index.await_args.kwargs["id"] == "d1"


async def test_get_document_found(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.get.return_value = {"_source": _make_document().model_dump(mode="json")}
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.document_id == "d1"


async def test_get_document_missing_returns_none(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    err = Exception("not found")
    err.status_code = 404  # type: ignore[attr-defined]
    fake_client.get.side_effect = err
    assert await store.get_document("missing") is None


async def test_get_document_other_error_raises(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.get.side_effect = Exception("boom")
    with pytest.raises(StorageError):
        await store.get_document("d1")


async def test_update_status(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.update_status("d1", DocumentStatus.READY)
    body = fake_client.update.await_args.kwargs["body"]
    assert body["doc"]["status"] == "ready"


async def test_delete_document_cascades(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.delete_document("d1")
    fake_client.delete_by_query.assert_awaited_once()
    fake_client.delete.assert_awaited_once()


async def test_index_chunks(store: OpenSearchStore, monkeypatch: pytest.MonkeyPatch) -> None:
    bulk = AsyncMock(return_value=(2, []))
    monkeypatch.setattr(os_module, "async_bulk", bulk)
    chunks = [
        Chunk(chunk_id="d1:0", document_id="d1", ordinal=0, snippet="a", embedding=[0.1]),
        Chunk(chunk_id="d1:1", document_id="d1", ordinal=1, snippet="b", embedding=[0.2]),
    ]
    assert await store.index_chunks(chunks) == 2


async def test_index_chunks_empty(store: OpenSearchStore) -> None:
    assert await store.index_chunks([]) == 0


async def test_hybrid_search_parses_hits(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "d1:0",
                    "_score": 1.5,
                    "_source": {
                        "document_id": "d1",
                        "chunk_id": "d1:0",
                        "snippet": "hello",
                        "title": "Invoice.pdf",
                        "doc_type": "invoice",
                        "category_paths": ["Finance"],
                    },
                }
            ]
        }
    }
    hits = await store.hybrid_search(query_text="hi", query_vector=[0.1], top_k=5)
    assert len(hits) == 1
    assert hits[0].document_id == "d1"
    assert hits[0].score == 1.5


async def test_update_content(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.update_content("d1", "# Markdown")
    body = fake_client.update.await_args.kwargs["body"]
    assert body["doc"]["content_markdown"] == "# Markdown"


async def test_update_metadata(store: OpenSearchStore, fake_client: MagicMock) -> None:
    from docstore.core.models import ExtractedValue

    await store.update_metadata(
        "d1",
        doc_type="invoice",
        extracted_values=[ExtractedValue(key="n", type="identifier", value="1")],
        folder_structure=["Finance/Invoices"],
        category_paths=["Finance/Invoices"],
    )
    body = fake_client.update.await_args.kwargs["body"]
    assert body["doc"]["doc_type"] == "invoice"
    assert body["doc"]["extracted_values"][0]["key"] == "n"
    assert body["doc"]["folder_structure"] == ["Finance/Invoices"]


async def test_find_by_hash_found(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {
        "hits": {"hits": [{"_source": _make_document().model_dump(mode="json")}]}
    }
    found = await store.find_by_hash("h")
    assert found is not None
    assert found.document_id == "d1"


async def test_find_by_hash_none(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {"hits": {"hits": []}}
    assert await store.find_by_hash("nope") is None


async def test_list_documents_total_dict(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {
        "hits": {
            "total": {"value": 5},
            "hits": [{"_source": _make_document().model_dump(mode="json")}],
        }
    }
    docs, total = await store.list_documents(page=1, page_size=10)
    assert total == 5
    assert len(docs) == 1


async def test_list_documents_total_int(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {"hits": {"total": 2, "hits": []}}
    docs, total = await store.list_documents(page=2, page_size=10)
    assert total == 2
    assert docs == []


async def test_scroll_documents_returns_cursor(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    source = _make_document().model_dump(mode="json")
    fake_client.search.return_value = {"hits": {"hits": [{"_source": source, "sort": [123, "d1"]}]}}
    docs, cursor = await store.scroll_documents(page_size=1)
    assert len(docs) == 1
    # Full page (size==1) -> a cursor is returned for the next page.
    assert cursor == [123, "d1"]


async def test_scroll_documents_last_page_no_cursor(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    source = _make_document().model_dump(mode="json")
    fake_client.search.return_value = {"hits": {"hits": [{"_source": source, "sort": [123, "d1"]}]}}
    docs, cursor = await store.scroll_documents(page_size=10, search_after=[1, "a"])
    assert len(docs) == 1
    assert cursor is None
    assert fake_client.search.await_args.kwargs["body"]["search_after"] == [1, "a"]


async def test_category_terms(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {
        "aggregations": {
            "paths": {
                "buckets": [
                    {"key": "Finance/Invoices", "doc_count": 3},
                    {"key": "Insurance", "doc_count": 1},
                ]
            }
        }
    }
    terms = await store.category_terms()
    assert terms == [("Finance/Invoices", 3), ("Insurance", 1)]


async def test_list_documents_in_category_subtree(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {
        "hits": {
            "total": {"value": 1},
            "hits": [{"_source": _make_document().model_dump(mode="json")}],
        }
    }
    _docs, total = await store.list_documents_in_category(
        category_path="Finance", include_subtree=True, page=1, page_size=10
    )
    assert total == 1
    query = fake_client.search.await_args.kwargs["body"]["query"]
    # Subtree search should use a bool/should with a prefix clause.
    assert "should" in query["bool"]["filter"][0]["bool"]


async def test_list_documents_in_category_exact(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.search.return_value = {"hits": {"total": 0, "hits": []}}
    docs, total = await store.list_documents_in_category(
        category_path="Finance", include_subtree=False, page=1, page_size=10
    )
    assert (docs, total) == ([], 0)
    query = fake_client.search.await_args.kwargs["body"]["query"]
    assert query["bool"]["filter"][0] == {"term": {"category_paths": "Finance"}}


async def test_bootstrap_tops_up_chunk_title_mapping(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    await store.bootstrap()
    fake_client.indices.put_mapping.assert_awaited_once()
    body = fake_client.indices.put_mapping.await_args.kwargs["body"]
    assert "title" in body["properties"]


async def test_search_documents(store: OpenSearchStore, fake_client: MagicMock) -> None:
    fake_client.search.return_value = {
        "hits": {
            "total": {"value": 1},
            "hits": [{"_source": _make_document().model_dump(mode="json")}],
        }
    }
    docs, total = await store.search_documents(
        query="invoice", page=1, page_size=10, doc_type="invoice", title="Invoice.pdf"
    )
    assert total == 1
    assert docs[0].document_id == "d1"
    filters = fake_client.search.await_args.kwargs["body"]["query"]["bool"]["filter"]
    assert {"term": {"doc_type": "invoice"}} in filters
    assert {"term": {"title.keyword": "Invoice.pdf"}} in filters


async def test_update_document_fields_propagates_to_chunks(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.get.return_value = {"_source": _make_document().model_dump(mode="json")}
    await store.update_document_fields(
        "d1",
        doc_type="contract",
        category_paths=["Legal/Contracts"],
    )
    fake_client.update.assert_awaited_once()
    doc_body = fake_client.update.await_args.kwargs["body"]["doc"]
    assert doc_body["doc_type"] == "contract"
    # Propagated to chunks because doc_type/category changed.
    fake_client.update_by_query.assert_awaited_once()


async def test_update_document_fields_no_propagation_for_folder_only(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.get.return_value = {"_source": _make_document().model_dump(mode="json")}
    await store.update_document_fields("d1", folder_structure=["A/B"])
    fake_client.update.assert_awaited_once()
    fake_client.update_by_query.assert_not_awaited()


async def test_update_document_fields_missing_raises(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fake_client.get.side_effect = None
    err = Exception("not found")
    err.status_code = 404  # type: ignore[attr-defined]
    fake_client.get.side_effect = err
    with pytest.raises(NotFoundError):
        await store.update_document_fields("missing", doc_type="x")


async def test_delete_chunks(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.delete_chunks("d1")
    fake_client.delete_by_query.assert_awaited_once()
    body = fake_client.delete_by_query.await_args.kwargs["body"]
    assert body["query"]["term"]["document_id"] == "d1"


async def test_write_listener_fires_on_index_and_delete(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fired: list[str] = []
    store.register_write_listener(lambda: fired.append("x"))
    await store.index_document(_make_document())
    await store.delete_document("d1")
    # index_document + delete_document each notify once.
    assert len(fired) == 2


async def test_write_listener_fires_on_metadata_update(
    store: OpenSearchStore, fake_client: MagicMock
) -> None:
    fired: list[str] = []
    store.register_write_listener(lambda: fired.append("x"))
    fake_client.get.return_value = {"_source": _make_document().model_dump(mode="json")}
    await store.update_document_fields("d1", category_paths=["A/B"])
    assert fired == ["x"]


async def test_close(store: OpenSearchStore, fake_client: MagicMock) -> None:
    await store.close()
    fake_client.close.assert_awaited_once()


def test_client_lazy_build() -> None:
    cfg = OpenSearchConfig(hosts="http://localhost:9200", password="")
    store = OpenSearchStore(cfg)
    client: Any = store.client
    assert client is not None
