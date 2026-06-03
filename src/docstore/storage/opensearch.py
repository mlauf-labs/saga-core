"""OpenSearch storage: index bootstrap, document/chunk CRUD, hybrid search (FR-25).

Manages the ``documents`` (keyword) and ``document_chunks`` (kNN) indices and the
hybrid search pipeline. Deleting a document cascades to its chunks (FR-26).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from opensearchpy import AsyncOpenSearch
from opensearchpy.helpers import async_bulk

from docstore.core.errors import StorageError
from docstore.core.logging import get_logger
from docstore.core.models import Chunk, Document, DocumentStatus, SearchHit
from docstore.storage.mappings import (
    build_hybrid_query,
    chunk_index_body,
    document_index_body,
    hybrid_pipeline_body,
)

if TYPE_CHECKING:
    from docstore.core.config import OpenSearchConfig
    from docstore.core.models import ExtractedValue

_log = get_logger("docstore.storage.opensearch")


def _parse_hosts(hosts: str) -> list[dict[str, Any]]:
    """Parse a comma-separated list of host URLs into opensearch-py host dicts."""
    parsed: list[dict[str, Any]] = []
    for raw in hosts.split(","):
        url = raw.strip()
        if not url:
            continue
        split = urlsplit(url if "://" in url else f"http://{url}")
        use_ssl = split.scheme == "https"
        parsed.append(
            {
                "host": split.hostname or "localhost",
                "port": split.port or (443 if use_ssl else 9200),
                "use_ssl": use_ssl,
            }
        )
    if not parsed:
        raise StorageError("No valid OpenSearch hosts configured (opensearch.hosts).")
    return parsed


class OpenSearchStore:
    """Typed async wrapper around the OpenSearch client."""

    def __init__(self, config: OpenSearchConfig, client: AsyncOpenSearch | None = None) -> None:
        self._config = config
        self._client = client

    @property
    def client(self) -> AsyncOpenSearch:
        if self._client is None:
            hosts = _parse_hosts(self._config.hosts)
            http_auth = (
                (self._config.username, self._config.password) if self._config.password else None
            )
            self._client = AsyncOpenSearch(
                hosts=hosts,
                http_auth=http_auth,
                verify_certs=self._config.verify_certs,
                ssl_show_warn=False,
            )
        return self._client

    async def bootstrap(self) -> None:
        """Create both indices (if missing) and the hybrid search pipeline."""
        await self._ensure_index(self._config.document_index, document_index_body())
        await self._ensure_index(self._config.chunk_index, chunk_index_body(self._config))
        await self._ensure_pipeline()

    async def _ensure_index(self, name: str, body: dict[str, Any]) -> None:
        try:
            if not await self.client.indices.exists(index=name):
                await self.client.indices.create(index=name, body=body)
                _log.info("index_created", index=name)
        except Exception as exc:
            raise StorageError(f"Failed to create OpenSearch index '{name}': {exc}") from exc

    async def _ensure_pipeline(self) -> None:
        name = self._config.hybrid_pipeline
        try:
            await self.client.transport.perform_request(
                "PUT",
                f"/_search/pipeline/{name}",
                body=hybrid_pipeline_body(self._config),
            )
            _log.info("search_pipeline_ready", pipeline=name)
        except Exception as exc:
            raise StorageError(
                f"Failed to create OpenSearch hybrid search pipeline '{name}': {exc}"
            ) from exc

    async def index_document(self, document: Document) -> None:
        """Create or replace a document record in the document index."""
        try:
            await self.client.index(
                index=self._config.document_index,
                id=document.document_id,
                body=document.model_dump(mode="json"),
                refresh=True,
            )
        except Exception as exc:
            raise StorageError(f"Failed to index document '{document.document_id}': {exc}") from exc

    async def get_document(self, document_id: str) -> Document | None:
        """Return a document by id, or ``None`` if it does not exist."""
        try:
            response = await self.client.get(index=self._config.document_index, id=document_id)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise StorageError(f"Failed to fetch document '{document_id}': {exc}") from exc
        return Document.model_validate(response["_source"])

    async def update_status(
        self, document_id: str, status: DocumentStatus, error: str | None = None
    ) -> None:
        """Update a document's processing status (FR-12)."""
        body = {
            "doc": {
                "status": status.value,
                "error": error,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        }
        try:
            await self.client.update(
                index=self._config.document_index, id=document_id, body=body, refresh=True
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to update status for document '{document_id}': {exc}"
            ) from exc

    async def update_content(self, document_id: str, content_markdown: str) -> None:
        """Persist the converted Markdown text on a document record (FR-4)."""
        body = {
            "doc": {
                "content_markdown": content_markdown,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        }
        try:
            await self.client.update(
                index=self._config.document_index, id=document_id, body=body, refresh=True
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to persist converted content for document '{document_id}': {exc}"
            ) from exc

    async def update_metadata(
        self,
        document_id: str,
        *,
        doc_type: str,
        extracted_values: list[ExtractedValue],
        folder_structure: list[str],
        category_paths: list[str],
    ) -> None:
        """Persist LLM-extracted metadata on a document record (FR-14/15/16)."""
        body = {
            "doc": {
                "doc_type": doc_type,
                "extracted_values": [v.model_dump(mode="json") for v in extracted_values],
                "folder_structure": folder_structure,
                "category_paths": category_paths,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        }
        try:
            await self.client.update(
                index=self._config.document_index, id=document_id, body=body, refresh=True
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to persist metadata for document '{document_id}': {exc}"
            ) from exc

    async def index_chunks(self, chunks: list[Chunk]) -> int:
        """Bulk-index chunk/vector records. Returns the number indexed."""
        if not chunks:
            return 0
        actions = [
            {
                "_index": self._config.chunk_index,
                "_id": chunk.chunk_id,
                "_source": chunk.model_dump(mode="json"),
            }
            for chunk in chunks
        ]
        try:
            success, _ = await async_bulk(self.client, actions, refresh=True)
        except Exception as exc:
            raise StorageError(f"Failed to bulk-index {len(chunks)} chunks: {exc}") from exc
        return int(success)

    async def delete_document(self, document_id: str) -> None:
        """Delete a document and all of its chunks (cascade, FR-26)."""
        try:
            await self.client.delete_by_query(
                index=self._config.chunk_index,
                body={"query": {"term": {"document_id": document_id}}},
                refresh=True,
                conflicts="proceed",
            )
            await self.client.delete(
                index=self._config.document_index,
                id=document_id,
                refresh=True,
                ignore=[404],
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to delete document '{document_id}' and its chunks: {exc}"
            ) from exc

    async def category_terms(self) -> list[tuple[str, int]]:
        """Return all category paths with their document counts (FR-22).

        Uses a terms aggregation over ``category_paths`` (a materialised-path field),
        from which the tree is derived in the service layer.
        """
        body = {
            "size": 0,
            "aggs": {"paths": {"terms": {"field": "category_paths", "size": 10000}}},
        }
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Failed to aggregate category paths: {exc}") from exc
        buckets = response["aggregations"]["paths"]["buckets"]
        return [(bucket["key"], int(bucket["doc_count"])) for bucket in buckets]

    async def list_documents_in_category(
        self, *, category_path: str, include_subtree: bool, page: int, page_size: int
    ) -> tuple[list[Document], int]:
        """Return a page of documents in a category branch (FR-22)."""
        if include_subtree:
            category_filter: dict[str, Any] = {
                "bool": {
                    "should": [
                        {"term": {"category_paths": category_path}},
                        {"prefix": {"category_paths": f"{category_path}/"}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        else:
            category_filter = {"term": {"category_paths": category_path}}
        body = {
            "from": max(page - 1, 0) * page_size,
            "size": page_size,
            "sort": [{"created_at": {"order": "desc"}}],
            "query": {"bool": {"filter": [category_filter]}},
        }
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(
                f"Failed to list documents in category '{category_path}': {exc}"
            ) from exc
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]
        total_count = total["value"] if isinstance(total, dict) else int(total)
        documents = [Document.model_validate(hit["_source"]) for hit in hits]
        return documents, total_count

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[SearchHit]:
        """Run hybrid (BM25 + kNN) search and return ranked snippets (FR-19)."""
        body = build_hybrid_query(
            query_text=query_text,
            query_vector=query_vector,
            top_k=top_k,
            filters=filters,
        )
        try:
            response = await self.client.search(
                index=self._config.chunk_index,
                body=body,
                params={"search_pipeline": self._config.hybrid_pipeline},
            )
        except Exception as exc:
            raise StorageError(f"Hybrid search failed: {exc}") from exc
        return [_hit_to_model(hit) for hit in response["hits"]["hits"]]

    async def find_by_hash(self, content_hash: str) -> Document | None:
        """Return the first document with a matching content hash, if any (FR-13)."""
        body = {"size": 1, "query": {"term": {"content_hash": content_hash}}}
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Failed to look up document by content hash: {exc}") from exc
        hits = response["hits"]["hits"]
        if not hits:
            return None
        return Document.model_validate(hits[0]["_source"])

    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]:
        """Return a page of documents (newest first) and the total count (FR-28)."""
        body = {
            "from": max(page - 1, 0) * page_size,
            "size": page_size,
            "sort": [{"created_at": {"order": "desc"}}],
            "query": {"match_all": {}},
        }
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Failed to list documents: {exc}") from exc
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]
        total_count = total["value"] if isinstance(total, dict) else int(total)
        documents = [Document.model_validate(hit["_source"]) for hit in hits]
        return documents, total_count

    async def close(self) -> None:
        """Close the underlying client connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None


def _hit_to_model(hit: dict[str, Any]) -> SearchHit:
    source = hit.get("_source", {})
    return SearchHit(
        document_id=source.get("document_id", ""),
        chunk_id=source.get("chunk_id", hit.get("_id", "")),
        snippet=source.get("snippet", ""),
        score=float(hit.get("_score", 0.0)),
        title=source.get("title", ""),
        doc_type=source.get("doc_type"),
        category_paths=source.get("category_paths", []),
    )
