"""Search service: hybrid retrieval + category browsing (FR-19/20/21/22).

Shared by the REST routes and the MCP tools so both expose identical behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docstore.core.logging import get_logger
from docstore.search.tree import build_category_tree
from docstore.storage.mappings import build_filters

if TYPE_CHECKING:
    from docstore.core.models import CategoryNode, Document, ExtractedValue, SearchHit
    from docstore.embeddings import EmbeddingProvider
    from docstore.storage import OpenSearchStore

_log = get_logger("docstore.search")


class SearchService:
    """Coordinates query embedding, hybrid search, and category browsing."""

    def __init__(
        self,
        *,
        opensearch: OpenSearchStore,
        embedder: EmbeddingProvider,
        default_top_k: int = 10,
        max_top_k: int = 50,
    ) -> None:
        self._opensearch = opensearch
        self._embedder = embedder
        self._default_top_k = default_top_k
        self._max_top_k = max_top_k

    def _bounded_top_k(self, top_k: int | None) -> int:
        if top_k is None or top_k <= 0:
            return self._default_top_k
        return min(top_k, self._max_top_k)

    async def hybrid_search(
        self,
        *,
        query: str,
        top_k: int | None = None,
        doc_type: str | None = None,
        category_path: str | None = None,
        title: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[SearchHit]:
        """Embed the query and run hybrid (keyword + vector) search (FR-19/20/21)."""
        size = self._bounded_top_k(top_k)
        vectors = await self._embedder.embed([query])
        query_vector = vectors[0] if vectors else []
        filter_clauses = build_filters(
            doc_type=doc_type,
            category_path=category_path,
            title=title,
            extracted_values=filters,
        )
        hits = await self._opensearch.hybrid_search(
            query_text=query,
            query_vector=query_vector,
            top_k=size,
            filters=filter_clauses or None,
        )
        _log.info("search", query_len=len(query), results=len(hits), top_k=size)
        return hits

    async def get_category_tree(
        self, *, prefix: str | None = None, max_depth: int | None = None
    ) -> list[CategoryNode]:
        """Return the derived category tree (FR-22)."""
        terms = await self._opensearch.category_terms()
        return build_category_tree(terms, prefix=prefix, max_depth=max_depth)

    async def list_documents_in_category(
        self,
        *,
        category_path: str,
        include_subtree: bool = True,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Document], int]:
        """List documents in a category branch (FR-22)."""
        return await self._opensearch.list_documents_in_category(
            category_path=category_path,
            include_subtree=include_subtree,
            page=page,
            page_size=page_size,
        )

    async def get_document(self, document_id: str) -> Document | None:
        """Fetch a single document by id (FR-23)."""
        return await self._opensearch.get_document(document_id)

    async def search_documents(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        page_size: int = 25,
        doc_type: str | None = None,
        category_path: str | None = None,
        title: str | None = None,
        status: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> tuple[list[Document], int]:
        """Keyword search over document title/content/metadata with filters (FR-20)."""
        return await self._opensearch.search_documents(
            query=query,
            page=page,
            page_size=page_size,
            doc_type=doc_type,
            category_path=category_path,
            title=title,
            status=status,
            extracted_values=filters,
        )

    async def update_document_metadata(
        self,
        document_id: str,
        *,
        doc_type: str | None = None,
        extracted_values: list[ExtractedValue] | None = None,
        folder_structure: list[str] | None = None,
        category_paths: list[str] | None = None,
    ) -> Document:
        """Patch editable document metadata, propagating to chunks (FR-20)."""
        return await self._opensearch.update_document_fields(
            document_id,
            doc_type=doc_type,
            extracted_values=extracted_values,
            folder_structure=folder_structure,
            category_paths=category_paths,
        )
