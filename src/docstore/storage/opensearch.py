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

from docstore.core.errors import NotFoundError, StorageError
from docstore.core.logging import get_logger
from docstore.core.models import Chunk, Document, DocumentStatus, SearchHit
from docstore.storage.mappings import (
    build_document_filters,
    build_document_search_body,
    build_hybrid_query,
    build_value_terms,
    chunk_index_body,
    document_index_body,
    hybrid_pipeline_body,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
        # Callbacks invoked after writes that can change the set of categories, so
        # caches (e.g. the category catalog) can invalidate themselves (FR-16).
        self._write_listeners: list[Callable[[], None]] = []

    def register_write_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback invoked after category-affecting writes."""
        self._write_listeners.append(listener)

    def _notify_write(self) -> None:
        for listener in self._write_listeners:
            listener()

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
        await self._ensure_chunk_fields()
        await self._ensure_pipeline()

    async def _ensure_chunk_fields(self) -> None:
        """Idempotently add newer chunk fields (e.g. ``title``) to an existing index."""
        try:
            await self.client.indices.put_mapping(
                index=self._config.chunk_index,
                body={
                    "properties": {
                        "title": {
                            "type": "text",
                            "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                        }
                    }
                },
            )
        except Exception as exc:
            _log.warning("chunk_mapping_update_failed", error=str(exc))

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
        self._notify_write()

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
        self._notify_write()

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
        self._notify_write()

    async def scroll_documents(
        self, *, page_size: int, search_after: list[Any] | None = None
    ) -> tuple[list[Document], list[Any] | None]:
        """Page through all documents using ``search_after`` cursor pagination (FR-28).

        Returns the page of documents and the cursor for the next page (``None`` when
        the last page has been reached).
        """
        body: dict[str, Any] = {
            "size": page_size,
            "sort": [
                {"created_at": {"order": "asc"}},
                {"document_id": {"order": "asc"}},
            ],
            "query": {"match_all": {}},
        }
        if search_after is not None:
            body["search_after"] = search_after
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Failed to scroll documents: {exc}") from exc
        hits = response["hits"]["hits"]
        documents = [Document.model_validate(hit["_source"]) for hit in hits]
        next_cursor = hits[-1]["sort"] if len(hits) == page_size and hits else None
        return documents, next_cursor

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

    async def search_documents(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
        doc_type: str | None = None,
        category_path: str | None = None,
        title: str | None = None,
        status: str | None = None,
        extracted_values: dict[str, str] | None = None,
    ) -> tuple[list[Document], int]:
        """Keyword document search over title/content/metadata with filters (FR-20)."""
        filters = build_document_filters(
            doc_type=doc_type,
            category_path=category_path,
            title=title,
            status=status,
            extracted_values=extracted_values,
        )
        body = build_document_search_body(
            query=query,
            filters=filters,
            from_=max(page - 1, 0) * page_size,
            size=page_size,
        )
        try:
            response = await self.client.search(index=self._config.document_index, body=body)
        except Exception as exc:
            raise StorageError(f"Document search failed: {exc}") from exc
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]
        total_count = total["value"] if isinstance(total, dict) else int(total)
        documents = [Document.model_validate(hit["_source"]) for hit in hits]
        return documents, total_count

    async def update_document_fields(
        self,
        document_id: str,
        *,
        doc_type: str | None = None,
        extracted_values: list[ExtractedValue] | None = None,
        folder_structure: list[str] | None = None,
        category_paths: list[str] | None = None,
    ) -> Document:
        """Patch editable metadata on a document and propagate to its chunks (FR-20).

        Only provided fields are changed. Changes to ``doc_type``, ``category_paths``
        or ``extracted_values`` are propagated to the document's chunks so search
        filters stay consistent. Returns the refreshed document.
        """
        existing = await self.get_document(document_id)
        if existing is None:
            raise NotFoundError(f"Document '{document_id}' was not found.")

        doc: dict[str, Any] = {"updated_at": datetime.now(UTC).isoformat()}
        if doc_type is not None:
            doc["doc_type"] = doc_type
        if extracted_values is not None:
            doc["extracted_values"] = [v.model_dump(mode="json") for v in extracted_values]
        if folder_structure is not None:
            doc["folder_structure"] = folder_structure
        if category_paths is not None:
            doc["category_paths"] = category_paths

        try:
            await self.client.update(
                index=self._config.document_index,
                id=document_id,
                body={"doc": doc},
                refresh=True,
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to update metadata for document '{document_id}': {exc}"
            ) from exc

        if doc_type is not None or category_paths is not None or extracted_values is not None:
            await self._propagate_to_chunks(
                document_id,
                doc_type=doc_type,
                category_paths=category_paths,
                value_terms=(
                    build_value_terms(extracted_values) if extracted_values is not None else None
                ),
            )

        self._notify_write()
        updated = await self.get_document(document_id)
        if updated is None:  # pragma: no cover - just updated successfully
            raise StorageError(f"Document '{document_id}' vanished after update.")
        return updated

    async def _propagate_to_chunks(
        self,
        document_id: str,
        *,
        doc_type: str | None,
        category_paths: list[str] | None,
        value_terms: list[str] | None,
    ) -> None:
        """Update denormalised fields on a document's chunks via update_by_query."""
        source_lines: list[str] = []
        params: dict[str, Any] = {}
        if doc_type is not None:
            source_lines.append("ctx._source.doc_type = params.doc_type;")
            params["doc_type"] = doc_type
        if category_paths is not None:
            source_lines.append("ctx._source.category_paths = params.category_paths;")
            params["category_paths"] = category_paths
        if value_terms is not None:
            source_lines.append("ctx._source.value_terms = params.value_terms;")
            params["value_terms"] = value_terms
        if not source_lines:
            return
        try:
            await self.client.update_by_query(
                index=self._config.chunk_index,
                body={
                    "query": {"term": {"document_id": document_id}},
                    "script": {
                        "source": " ".join(source_lines),
                        "lang": "painless",
                        "params": params,
                    },
                },
                refresh=True,
                conflicts="proceed",
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to propagate metadata to chunks of '{document_id}': {exc}"
            ) from exc

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
