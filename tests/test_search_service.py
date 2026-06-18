"""Unit tests for the fused (RRF) hybrid search service and RRF helper."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from saga.core.errors import ValidationError
from saga.core.models import (
    Document,
    DocumentHit,
    DocumentStatus,
    FolderNode,
    FolderRef,
    SearchHit,
)
from saga.search.service import SearchService, reciprocal_rank_fusion

# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #


class FakeOpenSearch:
    """Records calls and returns canned keyword/semantic hit lists."""

    def __init__(
        self,
        *,
        keyword_hits: list[DocumentHit] | None = None,
        semantic_hits: list[SearchHit] | None = None,
        document_search_result: tuple[list[str], int] | None = None,
    ) -> None:
        self.keyword_hits = keyword_hits or []
        self.semantic_hits = semantic_hits or []
        self.document_search_result = document_search_result or ([], 0)
        self.keyword_calls: list[dict[str, object]] = []
        self.semantic_calls: list[dict[str, object]] = []
        self.document_search_calls: list[dict[str, object]] = []

    async def keyword_search(
        self,
        *,
        query: str,
        fields: list[str],
        default_operator: str,
        top_k: int,
        filters: list[dict[str, object]] | None,
    ) -> list[DocumentHit]:
        self.keyword_calls.append(
            {
                "query": query,
                "fields": fields,
                "default_operator": default_operator,
                "top_k": top_k,
                "filters": filters,
            }
        )
        return self.keyword_hits

    async def semantic_search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filters: list[dict[str, object]] | None,
    ) -> list[SearchHit]:
        self.semantic_calls.append(
            {"query_vector": query_vector, "top_k": top_k, "filters": filters}
        )
        return self.semantic_hits

    async def document_search(
        self,
        *,
        query: str | None,
        filters: list[dict[str, object]],
        from_: int,
        size: int,
    ) -> tuple[list[str], int]:
        self.document_search_calls.append(
            {"query": query, "filters": filters, "from_": from_, "size": size}
        )
        return self.document_search_result


class FakeDB:
    """Returns seeded documents and a canned folder tree; records calls."""

    def __init__(
        self,
        *,
        documents: dict[str, Document] | None = None,
        folder_tree_result: list[FolderNode] | None = None,
    ) -> None:
        self.documents = documents or {}
        self.folder_tree_result = folder_tree_result or []
        self.get_document_calls: list[str] = []
        self.folder_tree_calls: list[dict[str, object]] = []
        self.list_documents_calls: list[dict[str, object]] = []

    async def get_document(self, document_id: str) -> Document | None:
        self.get_document_calls.append(document_id)
        return self.documents.get(document_id)

    async def folder_tree(
        self, *, prefix: str | None = None, max_depth: int | None = None
    ) -> list[FolderNode]:
        self.folder_tree_calls.append({"prefix": prefix, "max_depth": max_depth})
        return self.folder_tree_result

    async def list_documents_in_folder(
        self,
        folder_id: str,
        *,
        include_subtree: bool = True,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Document], int]:
        self.list_documents_calls.append(
            {
                "folder_id": folder_id,
                "include_subtree": include_subtree,
                "page": page,
                "page_size": page_size,
            }
        )
        docs = list(self.documents.values())
        return docs, len(docs)


class FakeEmbedder:
    """Async embedder that records the texts it was asked to embed."""

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [0.1, 0.2, 0.3]
        self.embed_calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(texts)
        return [self.vector for _ in texts]


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _doc(
    doc_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    doc_type: str | None = "invoice",
    folders: list[FolderRef] | None = None,
) -> Document:
    now = datetime.now(UTC)
    return Document(
        document_id=doc_id,
        title=title or f"Title {doc_id}",
        mime_type="application/pdf",
        size_bytes=10,
        content_hash=f"hash-{doc_id}",
        minio_object=f"saga-originals/{doc_id}",
        status=DocumentStatus.READY,
        summary=summary or f"Summary {doc_id}",
        doc_type=doc_type,
        folders=folders if folders is not None else [FolderRef(folder_id=f"f-{doc_id}", name="F")],
        created_at=now,
        updated_at=now,
    )


def _service(
    opensearch: FakeOpenSearch,
    db: FakeDB,
    embedder: FakeEmbedder,
    **kwargs: object,
) -> SearchService:
    return SearchService(
        opensearch=opensearch,  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
        default_top_k=kwargs.pop("default_top_k", 10),  # type: ignore[arg-type]
        max_top_k=kwargs.pop("max_top_k", 20),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# reciprocal_rank_fusion (unit)                                                 #
# --------------------------------------------------------------------------- #


def test_rrf_scores_single_ranking() -> None:
    scores = reciprocal_rank_fusion([["a", "b", "c"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63)
    # Earlier ranks score higher.
    assert scores["a"] > scores["b"] > scores["c"]


def test_rrf_fuses_multiple_rankings() -> None:
    # "a" is rank 1 in both lists, so it must beat everything else.
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]], k=60)
    assert scores["a"] == pytest.approx(2 / 61)
    # "c" is rank 2 in list two, "b" is rank 2 in list one -> equal here.
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 62)
    ordered = sorted(scores, key=lambda d: scores[d], reverse=True)
    assert ordered[0] == "a"


def test_rrf_respects_k() -> None:
    small_k = reciprocal_rank_fusion([["a"]], k=1)
    large_k = reciprocal_rank_fusion([["a"]], k=1000)
    assert small_k["a"] == pytest.approx(1 / 2)
    assert large_k["a"] == pytest.approx(1 / 1001)


# --------------------------------------------------------------------------- #
# Query validation                                                              #
# --------------------------------------------------------------------------- #


async def test_requires_at_least_one_query() -> None:
    opensearch = FakeOpenSearch()
    db = FakeDB()
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    with pytest.raises(ValidationError):
        await service.hybrid_search()
    # Whitespace-only queries also count as empty.
    with pytest.raises(ValidationError):
        await service.hybrid_search(keyword_query="  ", semantic_query="\t")

    assert opensearch.keyword_calls == []
    assert opensearch.semantic_calls == []
    assert embedder.embed_calls == []


# --------------------------------------------------------------------------- #
# Keyword-only / semantic-only paths                                            #
# --------------------------------------------------------------------------- #


async def test_keyword_only_skips_embedding() -> None:
    opensearch = FakeOpenSearch(
        keyword_hits=[DocumentHit(document_id="d1", title="t", score=2.0, snippet="kw snippet")]
    )
    db = FakeDB(documents={"d1": _doc("d1")})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    result = await service.hybrid_search(keyword_query="title:Rechnung AND 2024")

    assert len(result.results) == 1
    assert result.results[0].document_id == "d1"
    # The embedder and the semantic path are never touched.
    assert embedder.embed_calls == []
    assert opensearch.semantic_calls == []
    call = opensearch.keyword_calls[0]
    assert call["query"] == "title:Rechnung AND 2024"
    assert call["default_operator"] == "OR"


async def test_semantic_only_embeds_query() -> None:
    opensearch = FakeOpenSearch(
        semantic_hits=[
            SearchHit(document_id="d1", chunk_id="d1:0", snippet="s", score=1.0, title="t")
        ]
    )
    db = FakeDB(documents={"d1": _doc("d1")})
    embedder = FakeEmbedder(vector=[0.1, 0.2, 0.3])
    service = _service(opensearch, db, embedder)

    result = await service.hybrid_search(semantic_query="Wie hoch ist die Miete?")

    assert len(result.results) == 1
    assert result.results[0].document_id == "d1"
    assert embedder.embed_calls == [["Wie hoch ist die Miete?"]]
    assert opensearch.keyword_calls == []
    assert opensearch.semantic_calls[0]["query_vector"] == [0.1, 0.2, 0.3]


# --------------------------------------------------------------------------- #
# Fusion of both rankings                                                       #
# --------------------------------------------------------------------------- #


async def test_both_queries_are_fused_via_rrf() -> None:
    # d1 is rank 1 in BOTH rankings, so it must come out on top after fusion.
    # d3 ranks lower than d2 by keyword, but also appears in the semantic ranking,
    # so its fused score must overtake d2 (which only the keyword side returned).
    keyword_hits = [
        DocumentHit(document_id="d1", title="t1", score=3.0, snippet="kw1"),
        DocumentHit(document_id="d2", title="t2", score=2.0, snippet="kw2"),
        DocumentHit(document_id="d3", title="t3", score=1.0, snippet="kw3"),
    ]
    semantic_hits = [
        SearchHit(document_id="d1", chunk_id="d1:0", snippet="sem1", score=0.9, title="t1"),
        SearchHit(document_id="d3", chunk_id="d3:0", snippet="sem3", score=0.8, title="t3"),
    ]
    opensearch = FakeOpenSearch(keyword_hits=keyword_hits, semantic_hits=semantic_hits)
    db = FakeDB(documents={"d1": _doc("d1"), "d2": _doc("d2"), "d3": _doc("d3")})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    result = await service.hybrid_search(
        keyword_query="Mietvertrag", semantic_query="Kaution", doc_type="contract"
    )

    assert len(opensearch.keyword_calls) == 1
    assert len(opensearch.semantic_calls) == 1
    ordered = [item.document_id for item in result.results]
    assert ordered[0] == "d1"
    # d3 beats d2 because it scored on both rankings, not just the keyword one.
    assert ordered.index("d3") < ordered.index("d2")
    # Filters reach both retrieval paths.
    assert opensearch.keyword_calls[0]["filters"] is not None
    assert opensearch.semantic_calls[0]["filters"] is not None


# --------------------------------------------------------------------------- #
# Bounding + hydration                                                          #
# --------------------------------------------------------------------------- #


async def test_top_k_is_bounded_to_max() -> None:
    semantic_hits = [
        SearchHit(document_id=f"d{i}", chunk_id=f"d{i}:0", snippet="s", score=1.0, title="t")
        for i in range(30)
    ]
    opensearch = FakeOpenSearch(semantic_hits=semantic_hits)
    db = FakeDB(documents={f"d{i}": _doc(f"d{i}") for i in range(30)})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder, max_top_k=20)

    result = await service.hybrid_search(semantic_query="x", top_k=999)

    assert len(result.results) == 20


async def test_results_are_hydrated_from_db() -> None:
    doc = _doc(
        "d1",
        title="Mietvertrag 2024",
        summary="Wohnung in Berlin",
        doc_type="contract",
        folders=[
            FolderRef(folder_id="f-home", name="Home", is_primary=True),
            FolderRef(folder_id="f-legal", name="Legal"),
        ],
    )
    opensearch = FakeOpenSearch(
        keyword_hits=[DocumentHit(document_id="d1", title="ignored", score=1.0, snippet="kw")]
    )
    db = FakeDB(documents={"d1": doc})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    result = await service.hybrid_search(keyword_query="Mietvertrag")

    item = result.results[0]
    # Title/summary/doc_type/folder_ids come from the system of record, not the hit.
    assert item.title == "Mietvertrag 2024"
    assert item.summary == "Wohnung in Berlin"
    assert item.doc_type == "contract"
    assert item.folder_ids == ["f-home", "f-legal"]
    assert item.snippet == "kw"
    assert "d1" in db.get_document_calls


async def test_missing_documents_are_skipped() -> None:
    opensearch = FakeOpenSearch(
        keyword_hits=[
            DocumentHit(document_id="d1", title="t", score=2.0),
            DocumentHit(document_id="gone", title="t", score=1.0),
        ]
    )
    db = FakeDB(documents={"d1": _doc("d1")})  # "gone" is absent from the db
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    result = await service.hybrid_search(keyword_query="x")

    assert [item.document_id for item in result.results] == ["d1"]


# --------------------------------------------------------------------------- #
# search_documents + folder browsing                                            #
# --------------------------------------------------------------------------- #


async def test_search_documents_hydrates_from_db() -> None:
    opensearch = FakeOpenSearch(document_search_result=(["d2", "d1"], 2))
    db = FakeDB(documents={"d1": _doc("d1"), "d2": _doc("d2")})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    documents, total = await service.search_documents(query="invoice", page=1, page_size=25)

    assert total == 2
    assert [doc.document_id for doc in documents] == ["d2", "d1"]
    assert opensearch.document_search_calls[0]["from_"] == 0


# --------------------------------------------------------------------------- #
# Metadata filtering                                                            #
# --------------------------------------------------------------------------- #


def test_default_keyword_fields_include_metadata_text() -> None:
    opensearch = FakeOpenSearch()
    db = FakeDB()
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    assert "metadata_text" in service._keyword_fields


async def test_search_documents_threads_metadata_filter() -> None:
    opensearch = FakeOpenSearch(document_search_result=(["d1"], 1))
    db = FakeDB(documents={"d1": _doc("d1")})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    await service.search_documents(query="invoice", metadata={"project": "Apollo"})

    filters = cast("list[dict[str, Any]]", opensearch.document_search_calls[0]["filters"])
    nested = [f for f in filters if "nested" in f and f["nested"]["path"] == "metadata"]
    assert nested, "expected a nested metadata filter on the document search"
    must = nested[0]["nested"]["query"]["bool"]["filter"]
    assert {"term": {"metadata.key": "project"}} in must
    assert {"term": {"metadata.value.keyword": "Apollo"}} in must


async def test_hybrid_search_threads_metadata_filter() -> None:
    opensearch = FakeOpenSearch(
        keyword_hits=[DocumentHit(document_id="d1", title="t", score=1.0, snippet="kw")]
    )
    db = FakeDB(documents={"d1": _doc("d1")})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    await service.hybrid_search(keyword_query="invoice", metadata={"project": "Apollo"})

    filters = cast("list[dict[str, Any]]", opensearch.keyword_calls[0]["filters"])
    nested = [f for f in filters if "nested" in f and f["nested"]["path"] == "metadata"]
    assert nested, "expected a nested metadata filter on the keyword leg"


async def test_get_folder_tree_returns_db_tree() -> None:
    tree = [
        FolderNode(
            folder_id="finance",
            name="Finance",
            document_count=3,
            children=[FolderNode(folder_id="finance/invoices", name="Invoices", document_count=2)],
        )
    ]
    opensearch = FakeOpenSearch()
    db = FakeDB(folder_tree_result=tree)
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    result = await service.get_folder_tree(prefix="finance", max_depth=2)

    assert result[0].name == "Finance"
    assert result[0].children[0].name == "Invoices"
    assert db.folder_tree_calls[0] == {"prefix": "finance", "max_depth": 2}


async def test_get_document_delegates_to_db() -> None:
    opensearch = FakeOpenSearch()
    db = FakeDB(documents={"d1": _doc("d1")})
    embedder = FakeEmbedder()
    service = _service(opensearch, db, embedder)

    document = await service.get_document("d1")

    assert document is not None
    assert document.document_id == "d1"
