"""Search service: fused hybrid retrieval + folder browsing (FR-19/20/21/22).

Shared by the REST routes and the MCP tools so both expose identical behaviour.
Keyword (BM25 over the document projection) and semantic (kNN over chunks) rankings
are merged with Reciprocal Rank Fusion (RRF) into a single document-level ranking.
Documents/folders are hydrated from Postgres (the system of record).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saga.core.errors import ValidationError
from saga.core.logging import get_logger
from saga.core.models import HybridSearchResult, SearchResultItem
from saga.storage.mappings import build_document_filters, build_filters

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.models import Document, FolderNode
    from saga.embeddings import EmbeddingProvider
    from saga.storage import OpenSearchStore, PostgresStore

_log = get_logger("saga.search")


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int) -> dict[str, float]:
    """Fuse several ranked id lists into ``{id: score}`` via RRF (``1/(k+rank)``)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


class SearchService:
    """Coordinates query embedding, fused hybrid search, and folder browsing."""

    def __init__(
        self,
        *,
        opensearch: OpenSearchStore,
        db: PostgresStore,
        embedder: EmbeddingProvider,
        default_top_k: int = 10,
        max_top_k: int = 50,
        keyword_fields: list[str] | None = None,
        keyword_default_operator: str = "OR",
        rrf_k: int = 60,
    ) -> None:
        self._opensearch = opensearch
        self._db = db
        self._embedder = embedder
        self._default_top_k = default_top_k
        self._max_top_k = max_top_k
        self._keyword_fields = keyword_fields or [
            "title^3",
            "summary^2",
            "content_markdown",
            "doc_type",
            "metadata_text",
        ]
        self._keyword_default_operator = keyword_default_operator
        self._rrf_k = rrf_k

    def _bounded_top_k(self, top_k: int | None) -> int:
        if top_k is None or top_k <= 0:
            return self._default_top_k
        return min(top_k, self._max_top_k)

    async def hybrid_search(
        self,
        *,
        keyword_query: str | None = None,
        semantic_query: str | None = None,
        top_k: int | None = None,
        doc_type: str | None = None,
        folder_id: str | None = None,
        include_subtree: bool = True,
        title: str | None = None,
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        filters: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> HybridSearchResult:
        """Run a fused hybrid search and return one ranked document list (FR-19).

        ``keyword_query`` runs a ``query_string`` search over the document projection;
        ``semantic_query`` runs a kNN search over the chunk index. At least one must be
        given. The two rankings are merged with RRF and the top documents are hydrated
        from Postgres (with the best matching snippet attached).
        """
        keyword = (keyword_query or "").strip()
        semantic = (semantic_query or "").strip()
        if not keyword and not semantic:
            raise ValidationError(
                "Provide at least one of 'keyword_query' (query_string over the document "
                "projection) or 'semantic_query' (semantic search over chunks)."
            )

        size = self._bounded_top_k(top_k)
        # Retrieve a deeper pool so the fusion has signal beyond the final page.
        pool = max(size * 3, size)
        rankings: list[list[str]] = []
        snippets: dict[str, str] = {}

        if keyword:
            document_filters = build_document_filters(
                doc_type=doc_type,
                folder_id=folder_id,
                include_subtree=include_subtree,
                title=title,
                status=status,
                created_from=created_from,
                created_to=created_to,
                extracted_values=filters,
                metadata=metadata,
            )
            keyword_hits = await self._opensearch.keyword_search(
                query=keyword,
                fields=self._keyword_fields,
                default_operator=self._keyword_default_operator,
                top_k=pool,
                filters=document_filters or None,
            )
            rankings.append([hit.document_id for hit in keyword_hits])
            for hit in keyword_hits:
                if hit.snippet and hit.document_id not in snippets:
                    snippets[hit.document_id] = hit.snippet

        if semantic:
            chunk_filters = build_filters(
                doc_type=doc_type,
                folder_id=folder_id,
                include_subtree=include_subtree,
                title=title,
                status=status,
                created_from=created_from,
                created_to=created_to,
                extracted_values=filters,
            )
            vectors = await self._embedder.embed([semantic])
            query_vector = vectors[0] if vectors else []
            semantic_hits = await self._opensearch.semantic_search(
                query_vector=query_vector,
                top_k=pool,
                filters=chunk_filters or None,
            )
            # Roll chunk hits up to their parent document (best chunk per document).
            doc_ranking: list[str] = []
            seen: set[str] = set()
            for chunk_hit in semantic_hits:
                if chunk_hit.document_id in seen:
                    continue
                seen.add(chunk_hit.document_id)
                doc_ranking.append(chunk_hit.document_id)
                if chunk_hit.snippet and chunk_hit.document_id not in snippets:
                    snippets[chunk_hit.document_id] = chunk_hit.snippet
            rankings.append(doc_ranking)

        fused = reciprocal_rank_fusion(rankings, k=self._rrf_k)
        ordered_ids = sorted(fused, key=lambda doc_id: fused[doc_id], reverse=True)[:size]

        results: list[SearchResultItem] = []
        for doc_id in ordered_ids:
            document = await self._db.get_document(doc_id)
            if document is None:
                continue
            results.append(
                SearchResultItem(
                    document_id=doc_id,
                    title=document.title,
                    filename=document.filename,
                    score=fused[doc_id],
                    doc_type=document.doc_type,
                    summary=document.summary,
                    folder_ids=document.folder_ids,
                    snippet=snippets.get(doc_id),
                )
            )

        _log.info(
            "search",
            keyword=bool(keyword),
            semantic=bool(semantic),
            results=len(results),
            top_k=size,
        )
        return HybridSearchResult(results=results)

    async def get_folder_tree(
        self, *, prefix: str | None = None, max_depth: int | None = None
    ) -> list[FolderNode]:
        """Return the folder tree from Postgres with subtree document counts (FR-22)."""
        return await self._db.folder_tree(prefix=prefix, max_depth=max_depth)

    async def list_documents_in_folder(
        self,
        *,
        folder_id: str,
        include_subtree: bool = True,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Document], int]:
        """List documents in a folder branch (FR-22)."""
        return await self._db.list_documents_in_folder(
            folder_id,
            include_subtree=include_subtree,
            page=page,
            page_size=page_size,
        )

    async def get_document(self, document_id: str) -> Document | None:
        """Fetch a single document by id from the system of record (FR-23)."""
        return await self._db.get_document(document_id)

    async def search_documents(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        page_size: int = 25,
        doc_type: str | None = None,
        folder_id: str | None = None,
        include_subtree: bool = True,
        title: str | None = None,
        status: str | None = None,
        filters: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[list[Document], int]:
        """Keyword/filter search over the projection, hydrated from Postgres (FR-20)."""
        document_filters = build_document_filters(
            doc_type=doc_type,
            folder_id=folder_id,
            include_subtree=include_subtree,
            title=title,
            status=status,
            extracted_values=filters,
            metadata=metadata,
        )
        ids, total = await self._opensearch.document_search(
            query=query,
            filters=document_filters,
            from_=max(page - 1, 0) * page_size,
            size=page_size,
        )
        documents: list[Document] = []
        for doc_id in ids:
            document = await self._db.get_document(doc_id)
            if document is not None:
                documents.append(document)
        return documents, total
