"""OpenSearch search projection: index bootstrap, document/chunk projection, and
keyword/semantic/similarity search (FR-19/21/25).

OpenSearch is a *derived*, rebuildable view of the Postgres system of record. It holds
the ``documents`` projection (keyword/metadata + a summary embedding for document-level
similarity) and the ``document_chunks`` kNN index. It is never the source of truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from opensearchpy import AsyncOpenSearch
from opensearchpy.helpers import async_bulk

from saga.core.errors import StorageError
from saga.core.logging import get_logger
from saga.core.models import (
    Chunk,
    DocumentHit,
    SearchHit,
    SimilarDocument,
)
from saga.storage.mappings import (
    build_document_search_body,
    build_keyword_query_body,
    build_metadata_text,
    build_more_like_this_body,
    build_semantic_query_body,
    build_summary_knn_body,
    build_value_terms,
    chunk_index_body,
    document_index_body,
)

if TYPE_CHECKING:
    from saga.core.config import OpenSearchConfig
    from saga.core.models import Document

_log = get_logger("saga.storage.opensearch")


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
    """Typed async wrapper around the OpenSearch client (search projection only)."""

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
        """Create both projection indices, repairing an incompatible existing mapping.

        OpenSearch is a rebuildable projection, so if an existing index is missing a
        required ``knn_vector`` field (e.g. a stale ``documents`` index created before
        ``summary_embedding`` existed), the index is dropped and recreated with the
        current mapping. The projection must then be rebuilt from Postgres on the next
        ingest/update; the SoR is unaffected.
        """
        await self._ensure_index(
            self._config.document_index,
            document_index_body(self._config),
            knn_fields=("summary_embedding",),
        )
        await self._ensure_index(
            self._config.chunk_index,
            chunk_index_body(self._config),
            knn_fields=("embedding",),
        )

    async def _ensure_index(
        self, name: str, body: dict[str, Any], *, knn_fields: tuple[str, ...] = ()
    ) -> None:
        try:
            if not await self.client.indices.exists(index=name):
                await self.client.indices.create(index=name, body=body)
                _log.info("index_created", index=name)
                return
            missing = await self._incompatible_knn_fields(name, knn_fields)
            if missing:
                _log.warning("index_mapping_incompatible_recreating", index=name, fields=missing)
                await self.client.indices.delete(index=name)
                await self.client.indices.create(index=name, body=body)
                _log.info("index_recreated", index=name)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to create OpenSearch index '{name}': {exc}") from exc

    async def _incompatible_knn_fields(self, name: str, knn_fields: tuple[str, ...]) -> list[str]:
        """Return required kNN fields that are absent or not ``knn_vector`` on ``name``."""
        if not knn_fields:
            return []
        mapping = await self.client.indices.get_mapping(index=name)
        properties: dict[str, Any] = mapping.get(name, {}).get("mappings", {}).get("properties", {})
        return [
            field for field in knn_fields if properties.get(field, {}).get("type") != "knn_vector"
        ]

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        """Create or replace the search projection of a document.

        The projection denormalises folder membership (direct + ancestors) and,
        when available, the summary embedding used for document-level similarity.
        """
        source: dict[str, Any] = {
            "document_id": document.document_id,
            "title": document.title,
            "filename": document.filename,
            "summary": document.summary,
            "content_markdown": document.content_markdown,
            "doc_type": document.doc_type,
            "extracted_values": [v.model_dump(mode="json") for v in document.extracted_values],
            "folder_ids": document.folder_ids,
            "folder_ancestor_ids": folder_ancestor_ids,
            "primary_folder_id": document.primary_folder_id,
            "value_terms": build_value_terms(document.extracted_values),
            "metadata": [{"key": k, "value": v} for k, v in document.metadata.items()],
            "metadata_text": build_metadata_text(document.metadata),
            "minio_object": document.minio_object,
            "content_hash": document.content_hash,
            "mime_type": document.mime_type,
            "size_bytes": document.size_bytes,
            "status": document.status.value,
            "error": document.error,
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
        }
        if summary_embedding is not None:
            source["summary_embedding"] = summary_embedding
        try:
            await self.client.index(
                index=self._config.document_index,
                id=document.document_id,
                body=source,
                refresh=True,
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to project document '{document.document_id}': {exc}"
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

    async def delete_chunks(self, document_id: str) -> None:
        """Delete all chunks of a document (used before re-indexing on re-analysis)."""
        try:
            await self.client.delete_by_query(
                index=self._config.chunk_index,
                body={"query": {"term": {"document_id": document_id}}},
                refresh=True,
                conflicts="proceed",
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to delete chunks of document '{document_id}': {exc}"
            ) from exc

    async def delete_document(self, document_id: str) -> None:
        """Delete a document's projection and all of its chunks (cascade, FR-26)."""
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
                f"Failed to delete projection of document '{document_id}': {exc}"
            ) from exc

    async def keyword_search(
        self,
        *,
        query: str,
        fields: list[str],
        default_operator: str,
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[DocumentHit]:
        """Run a keyword ``query_string`` search over the document projection."""
        body = build_keyword_query_body(
            query=query,
            fields=fields,
            default_operator=default_operator,
            filters=filters or [],
            size=top_k,
        )
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Keyword search failed: {exc}") from exc
        return [_hit_to_document_hit(hit) for hit in response["hits"]["hits"]]

    async def semantic_search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[SearchHit]:
        """Run a pure kNN (semantic) search over the chunk index (FR-19/21)."""
        body = build_semantic_query_body(
            query_vector=query_vector,
            top_k=top_k,
            filters=filters,
        )
        try:
            response = await self.client.search(index=self._config.chunk_index, body=body)
        except Exception as exc:
            raise StorageError(f"Semantic search failed: {exc}") from exc
        return [_hit_to_model(hit) for hit in response["hits"]["hits"]]

    async def document_search(
        self,
        *,
        query: str | None,
        filters: list[dict[str, Any]],
        from_: int,
        size: int,
    ) -> tuple[list[str], int]:
        """Keyword/filter search over the document projection; returns (ids, total)."""
        body = build_document_search_body(query=query, filters=filters, from_=from_, size=size)
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Document search failed: {exc}") from exc
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]
        total_count = total["value"] if isinstance(total, dict) else int(total)
        ids = [hit.get("_source", {}).get("document_id", hit.get("_id", "")) for hit in hits]
        return ids, total_count

    async def similar_by_summary(
        self, *, query_vector: list[float], top_k: int, exclude_document_id: str | None = None
    ) -> list[SimilarDocument]:
        """Return documents most similar to ``query_vector`` by summary embedding (FR-16)."""
        body = build_summary_knn_body(
            query_vector=query_vector, top_k=top_k, exclude_document_id=exclude_document_id
        )
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Summary similarity search failed: {exc}") from exc
        return [_hit_to_similar(hit) for hit in response["hits"]["hits"]]

    async def similar_by_text(
        self, *, text: str, top_k: int, exclude_document_id: str | None = None
    ) -> list[SimilarDocument]:
        """Return documents lexically similar to ``text`` via more_like_this (FR-16)."""
        body = build_more_like_this_body(
            text=text, top_k=top_k, exclude_document_id=exclude_document_id
        )
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Lexical similarity search failed: {exc}") from exc
        return [_hit_to_similar(hit) for hit in response["hits"]["hits"]]

    async def index_stats(self) -> dict[str, dict[str, int]]:
        """Per-index document count and primary store size in bytes."""
        result = await self.client.indices.stats(metric="docs,store")
        out: dict[str, dict[str, int]] = {}
        for name, body in (result.get("indices") or {}).items():
            primaries = body.get("primaries", {})
            out[name] = {
                "docs": int(primaries.get("docs", {}).get("count", 0)),
                "size_bytes": int(primaries.get("store", {}).get("size_in_bytes", 0)),
            }
        return out

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
        filename=source.get("filename"),
        doc_type=source.get("doc_type"),
        folder_ids=source.get("folder_ids", []),
    )


def _hit_to_document_hit(hit: dict[str, Any]) -> DocumentHit:
    source = hit.get("_source", {})
    highlight = hit.get("highlight", {})
    fragments = highlight.get("content_markdown") if isinstance(highlight, dict) else None
    snippet = fragments[0] if fragments else None
    return DocumentHit(
        document_id=source.get("document_id", hit.get("_id", "")),
        title=source.get("title", ""),
        filename=source.get("filename"),
        score=float(hit.get("_score", 0.0)),
        doc_type=source.get("doc_type"),
        folder_ids=source.get("folder_ids", []),
        snippet=snippet,
    )


def _hit_to_similar(hit: dict[str, Any]) -> SimilarDocument:
    source = hit.get("_source", {})
    return SimilarDocument(
        document_id=source.get("document_id", hit.get("_id", "")),
        title=source.get("title", ""),
        filename=source.get("filename"),
        score=float(hit.get("_score", 0.0)),
        doc_type=source.get("doc_type"),
        summary=source.get("summary"),
        folder_ids=source.get("folder_ids", []),
        primary_folder_id=source.get("primary_folder_id"),
        value_terms=source.get("value_terms", []),
    )
