"""Unit tests for OpenSearch mapping and query builders."""

from __future__ import annotations

from docstore.core.config import OpenSearchConfig
from docstore.storage.mappings import (
    build_filters,
    build_hybrid_query,
    chunk_index_body,
    document_index_body,
    hybrid_pipeline_body,
)


def test_document_index_body_has_core_fields() -> None:
    props = document_index_body()["mappings"]["properties"]
    assert props["document_id"]["type"] == "keyword"
    assert props["extracted_values"]["type"] == "nested"
    assert props["content_markdown"]["type"] == "text"


def test_chunk_index_body_uses_config_vector_params() -> None:
    cfg = OpenSearchConfig(vector_dimension=1024, vector_engine="lucene")
    body = chunk_index_body(cfg)
    assert body["settings"]["index"]["knn"] is True
    embedding = body["mappings"]["properties"]["embedding"]
    assert embedding["dimension"] == 1024
    assert embedding["method"]["engine"] == "lucene"


def test_hybrid_pipeline_weights() -> None:
    cfg = OpenSearchConfig(hybrid_weights=[0.3, 0.7])
    processors = hybrid_pipeline_body(cfg)["phase_results_processors"]
    combo = processors[0]["normalization-processor"]["combination"]
    assert combo["parameters"]["weights"] == [0.3, 0.7]


def test_build_filters() -> None:
    filters = build_filters(
        doc_type="invoice",
        category_path="Finance/Invoices",
        extracted_values={"invoice_number": "123"},
    )
    assert {"term": {"doc_type": "invoice"}} in filters
    assert any("bool" in f for f in filters)
    assert {"term": {"extracted_values.invoice_number.keyword": "123"}} in filters


def test_build_hybrid_query_structure() -> None:
    body = build_hybrid_query(query_text="hello", query_vector=[0.1, 0.2], top_k=5)
    assert body["size"] == 5
    queries = body["query"]["hybrid"]["queries"]
    assert len(queries) == 2
    assert body["_source"]["excludes"] == ["embedding"]


def test_build_hybrid_query_with_filters_applies_to_both_clauses() -> None:
    filters = build_filters(doc_type="invoice")
    body = build_hybrid_query(query_text="hello", query_vector=[0.1], top_k=3, filters=filters)
    match_clause, knn_clause = body["query"]["hybrid"]["queries"]
    assert "filter" in match_clause["bool"]
    assert "filter" in knn_clause["knn"]["embedding"]
