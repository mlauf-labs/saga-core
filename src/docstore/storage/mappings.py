"""Pure builders for OpenSearch index mappings, the hybrid search pipeline, and
queries (FR-25). Kept side-effect free so they can be unit-tested without a cluster.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from docstore.core.config import OpenSearchConfig
    from docstore.core.models import ExtractedValue


def build_value_terms(values: Iterable[ExtractedValue]) -> list[str]:
    """Build denormalised ``key=value`` keyword terms for chunk metadata filtering.

    Includes both the raw and (when different) the normalised value so either can be
    matched by a filter (FR-20).
    """
    terms: list[str] = []
    for value in values:
        terms.append(f"{value.key}={value.value}")
        if value.normalized and value.normalized != value.value:
            terms.append(f"{value.key}={value.normalized}")
    return terms


def document_index_body() -> dict[str, Any]:
    """Mapping for the keyword/metadata document index."""
    return {
        "settings": {"index": {"number_of_shards": 1}},
        "mappings": {
            "properties": {
                "document_id": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                },
                "content_markdown": {"type": "text"},
                "doc_type": {"type": "keyword"},
                "extracted_values": {
                    "type": "nested",
                    "properties": {
                        "key": {"type": "keyword"},
                        "type": {"type": "keyword"},
                        "value": {
                            "type": "text",
                            "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                        },
                        "normalized": {"type": "keyword"},
                        "confidence": {"type": "float"},
                    },
                },
                "folder_structure": {"type": "keyword"},
                "category_paths": {"type": "keyword"},
                "minio_object": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "mime_type": {"type": "keyword"},
                "size_bytes": {"type": "long"},
                "status": {"type": "keyword"},
                "error": {"type": "text"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            }
        },
    }


def chunk_index_body(config: OpenSearchConfig) -> dict[str, Any]:
    """Mapping for the kNN-enabled chunk/vector index."""
    return {
        "settings": {"index": {"knn": True, "number_of_shards": 1}},
        "mappings": {
            "properties": {
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "snippet": {"type": "text"},
                "ordinal": {"type": "integer"},
                "title": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                },
                "doc_type": {"type": "keyword"},
                "category_paths": {"type": "keyword"},
                "value_terms": {"type": "keyword"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": config.vector_dimension,
                    "method": {
                        "name": "hnsw",
                        "space_type": config.vector_space_type,
                        "engine": config.vector_engine,
                        "parameters": {
                            "ef_construction": config.knn_ef_construction,
                            "m": config.knn_m,
                        },
                    },
                },
            }
        },
    }


def hybrid_pipeline_body(config: OpenSearchConfig) -> dict[str, Any]:
    """Search pipeline that normalizes + combines BM25 and kNN scores (FR-19)."""
    return {
        "description": "DocStore hybrid search normalization/combination",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": config.hybrid_normalization},
                    "combination": {
                        "technique": config.hybrid_combination,
                        "parameters": {"weights": list(config.hybrid_weights)},
                    },
                }
            }
        ],
    }


def build_filters(
    *,
    doc_type: str | None = None,
    category_path: str | None = None,
    title: str | None = None,
    extracted_values: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build OpenSearch filter clauses for metadata filtering (FR-20)."""
    filters: list[dict[str, Any]] = []
    if doc_type:
        filters.append({"term": {"doc_type": doc_type}})
    if title:
        filters.append({"term": {"title.keyword": title}})
    if category_path:
        # Match the exact path or any descendant path.
        filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"category_paths": category_path}},
                        {"prefix": {"category_paths": f"{category_path}/"}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    for key, value in (extracted_values or {}).items():
        # Chunks denormalise extracted values as ``key=value`` keyword terms (FR-20).
        filters.append({"term": {"value_terms": f"{key}={value}"}})
    return filters


def build_document_filters(
    *,
    doc_type: str | None = None,
    category_path: str | None = None,
    title: str | None = None,
    status: str | None = None,
    extracted_values: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build filter clauses for the document index (keyword + nested values, FR-20)."""
    filters: list[dict[str, Any]] = []
    if doc_type:
        filters.append({"term": {"doc_type": doc_type}})
    if title:
        filters.append({"term": {"title.keyword": title}})
    if status:
        filters.append({"term": {"status": status}})
    if category_path:
        filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"category_paths": category_path}},
                        {"prefix": {"category_paths": f"{category_path}/"}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    for key, value in (extracted_values or {}).items():
        filters.append(
            {
                "nested": {
                    "path": "extracted_values",
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"extracted_values.key": key}},
                                {"term": {"extracted_values.value.keyword": value}},
                            ]
                        }
                    },
                }
            }
        )
    return filters


def build_document_search_body(
    *,
    query: str | None,
    filters: list[dict[str, Any]],
    from_: int,
    size: int,
) -> dict[str, Any]:
    """Build a keyword document-search body over the document index (title/content/...).

    Matches across ``title`` (boosted), ``content_markdown``, ``doc_type``,
    ``category_paths`` and nested ``extracted_values.value``. An empty query matches
    all documents (filter-only browsing).
    """
    if query:
        should: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["title^3", "content_markdown", "doc_type", "category_paths"],
                }
            },
            {
                "nested": {
                    "path": "extracted_values",
                    "query": {"match": {"extracted_values.value": query}},
                }
            },
        ]
        match: dict[str, Any] = {"bool": {"should": should, "minimum_should_match": 1}}
    else:
        match = {"match_all": {}}
    return {
        "from": from_,
        "size": size,
        "sort": ["_score", {"created_at": {"order": "desc"}}],
        "query": {"bool": {"must": [match], "filter": filters}},
        "_source": {"excludes": ["embedding"]},
    }


def build_hybrid_query(
    *,
    query_text: str,
    query_vector: list[float],
    top_k: int,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a hybrid (BM25 + kNN) query body for the chunk index (FR-19)."""
    knn_clause: dict[str, Any] = {"knn": {"embedding": {"vector": query_vector, "k": top_k}}}
    match_clause: dict[str, Any] = {
        "multi_match": {"query": query_text, "fields": ["snippet", "title^2"]}
    }
    if filters:
        knn_clause["knn"]["embedding"]["filter"] = {"bool": {"filter": filters}}
        match_clause = {
            "bool": {"must": [match_clause], "filter": filters},
        }
    return {
        "size": top_k,
        "query": {"hybrid": {"queries": [match_clause, knn_clause]}},
        "_source": {"excludes": ["embedding"]},
    }
