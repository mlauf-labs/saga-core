"""Pure builders for OpenSearch index mappings and queries (FR-25).

OpenSearch is the rebuildable *search projection*: a keyword/metadata document index
(with a summary embedding for document-level similarity) and a kNN chunk index. The
builders are side-effect free so they can be unit-tested without a cluster.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from saga.core.config import OpenSearchConfig
    from saga.core.models import ExtractedValue


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


def document_index_body(config: OpenSearchConfig) -> dict[str, Any]:
    """Mapping for the keyword/metadata document projection (kNN-enabled for summary)."""
    return {
        "settings": {"index": {"knn": True, "number_of_shards": 1}},
        "mappings": {
            "properties": {
                "document_id": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                },
                "filename": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                },
                "summary": {"type": "text"},
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
                "folder_ids": {"type": "keyword"},
                "folder_ancestor_ids": {"type": "keyword"},
                "primary_folder_id": {"type": "keyword"},
                "value_terms": {"type": "keyword"},
                "minio_object": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "mime_type": {"type": "keyword"},
                "size_bytes": {"type": "long"},
                "status": {"type": "keyword"},
                "error": {"type": "text"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "summary_embedding": _knn_vector(config),
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
                "folder_ids": {"type": "keyword"},
                "folder_ancestor_ids": {"type": "keyword"},
                "value_terms": {"type": "keyword"},
                "status": {"type": "keyword"},
                "created_at": {"type": "date"},
                "mime_type": {"type": "keyword"},
                "size_bytes": {"type": "long"},
                "embedding": _knn_vector(config),
            }
        },
    }


def _knn_vector(config: OpenSearchConfig) -> dict[str, Any]:
    return {
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
    }


def build_folder_filter(folder_id: str, *, include_subtree: bool = True) -> dict[str, Any]:
    """Filter clause restricting to a folder (and, by default, its whole subtree).

    The projection denormalises every membership folder *and its ancestors* into
    ``folder_ancestor_ids``, so a subtree filter is a single term match on that field.
    An exact-folder filter matches ``folder_ids`` directly.
    """
    field = "folder_ancestor_ids" if include_subtree else "folder_ids"
    return {"term": {field: folder_id}}


def _build_date_range(created_from: str | None, created_to: str | None) -> dict[str, Any] | None:
    """Build a ``created_at`` range clause from optional ISO bounds (inclusive)."""
    bounds: dict[str, str] = {}
    if created_from:
        bounds["gte"] = created_from
    if created_to:
        bounds["lte"] = created_to
    if not bounds:
        return None
    return {"range": {"created_at": bounds}}


def build_filters(
    *,
    doc_type: str | None = None,
    folder_id: str | None = None,
    include_subtree: bool = True,
    title: str | None = None,
    status: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    extracted_values: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build OpenSearch filter clauses for the chunk index (FR-20)."""
    filters: list[dict[str, Any]] = []
    if doc_type:
        filters.append({"term": {"doc_type": doc_type}})
    if title:
        filters.append({"term": {"title.keyword": title}})
    if status:
        filters.append({"term": {"status": status}})
    if folder_id:
        filters.append(build_folder_filter(folder_id, include_subtree=include_subtree))
    date_range = _build_date_range(created_from, created_to)
    if date_range is not None:
        filters.append(date_range)
    for key, value in (extracted_values or {}).items():
        filters.append({"term": {"value_terms": f"{key}={value}"}})
    return filters


def build_document_filters(
    *,
    doc_type: str | None = None,
    folder_id: str | None = None,
    include_subtree: bool = True,
    title: str | None = None,
    status: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    extracted_values: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build filter clauses for the document projection (keyword + nested, FR-20)."""
    filters: list[dict[str, Any]] = []
    if doc_type:
        filters.append({"term": {"doc_type": doc_type}})
    if title:
        filters.append({"term": {"title.keyword": title}})
    if status:
        filters.append({"term": {"status": status}})
    if folder_id:
        filters.append(build_folder_filter(folder_id, include_subtree=include_subtree))
    date_range = _build_date_range(created_from, created_to)
    if date_range is not None:
        filters.append(date_range)
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
    """Build a keyword document-search body over the document projection."""
    if query:
        should: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "title^3",
                        "filename^2",
                        "summary^2",
                        "content_markdown",
                        "doc_type",
                    ],
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
        "_source": {"excludes": ["summary_embedding"]},
    }


def build_keyword_query_body(
    *,
    query: str,
    fields: list[str],
    default_operator: str,
    filters: list[dict[str, Any]],
    size: int,
) -> dict[str, Any]:
    """Build a keyword ``query_string`` body over the document projection (FR-19/20)."""
    return {
        "size": size,
        "query": {
            "bool": {
                "must": [
                    {
                        "query_string": {
                            "query": query,
                            "fields": fields,
                            "default_operator": default_operator,
                            "lenient": True,
                        }
                    }
                ],
                "filter": filters,
            }
        },
        "highlight": {
            "fields": {"content_markdown": {"fragment_size": 200, "number_of_fragments": 1}}
        },
        "_source": {"excludes": ["summary_embedding"]},
        "sort": ["_score", {"created_at": {"order": "desc"}}],
    }


def build_semantic_query_body(
    *,
    query_vector: list[float],
    top_k: int,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a pure kNN (semantic) query body for the chunk index (FR-19/21)."""
    knn: dict[str, Any] = {"vector": query_vector, "k": top_k}
    if filters:
        knn["filter"] = {"bool": {"filter": filters}}
    return {
        "size": top_k,
        "query": {"knn": {"embedding": knn}},
        "_source": {"excludes": ["embedding"]},
    }


def build_summary_knn_body(
    *,
    query_vector: list[float],
    top_k: int,
    exclude_document_id: str | None = None,
) -> dict[str, Any]:
    """Build a kNN query over document ``summary_embedding`` for similarity (FR-16)."""
    knn: dict[str, Any] = {"vector": query_vector, "k": top_k}
    if exclude_document_id is not None:
        knn["filter"] = {
            "bool": {"must_not": [{"term": {"document_id": exclude_document_id}}]}
        }
    return {
        "size": top_k,
        "query": {"knn": {"summary_embedding": knn}},
        "_source": {"excludes": ["summary_embedding", "content_markdown"]},
    }


def build_more_like_this_body(
    *,
    text: str,
    top_k: int,
    exclude_document_id: str | None = None,
) -> dict[str, Any]:
    """Build a ``more_like_this`` lexical-similarity query over the document projection."""
    mlt: dict[str, Any] = {
        "more_like_this": {
            "fields": ["title", "summary", "content_markdown"],
            "like": text,
            "min_term_freq": 1,
            "min_doc_freq": 1,
        }
    }
    must_not = (
        [{"term": {"document_id": exclude_document_id}}] if exclude_document_id is not None else []
    )
    return {
        "size": top_k,
        "query": {"bool": {"must": [mlt], "must_not": must_not}},
        "_source": {"excludes": ["summary_embedding", "content_markdown"]},
    }
