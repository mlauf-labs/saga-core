"""Unit tests for OpenSearch mapping and query builders."""

from __future__ import annotations

from docstore.core.config import OpenSearchConfig
from docstore.core.models import ExtractedValue
from docstore.storage.mappings import (
    build_document_filters,
    build_document_search_body,
    build_filters,
    build_hybrid_query,
    build_value_terms,
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


def test_chunk_index_body_has_title() -> None:
    props = chunk_index_body(OpenSearchConfig())["mappings"]["properties"]
    assert props["title"]["type"] == "text"
    assert props["title"]["fields"]["keyword"]["type"] == "keyword"


def test_build_filters() -> None:
    filters = build_filters(
        doc_type="invoice",
        category_path="Finance/Invoices",
        title="Invoice 1",
        extracted_values={"invoice_number": "123"},
    )
    assert {"term": {"doc_type": "invoice"}} in filters
    assert {"term": {"title.keyword": "Invoice 1"}} in filters
    assert any("bool" in f for f in filters)
    assert {"term": {"value_terms": "invoice_number=123"}} in filters


def test_build_value_terms_includes_raw_and_normalized() -> None:
    values = [
        ExtractedValue(key="amount", type="amount", value="100,00", normalized="100.00"),
        ExtractedValue(key="iban", type="iban", value="DE123"),
    ]
    terms = build_value_terms(values)
    assert "amount=100,00" in terms
    assert "amount=100.00" in terms
    assert "iban=DE123" in terms


def test_build_document_filters() -> None:
    filters = build_document_filters(
        doc_type="invoice",
        category_path="Finance",
        title="t",
        status="ready",
        extracted_values={"invoice_number": "123"},
    )
    assert {"term": {"doc_type": "invoice"}} in filters
    assert {"term": {"title.keyword": "t"}} in filters
    assert {"term": {"status": "ready"}} in filters
    assert any(f.get("nested") for f in filters)


def test_build_document_search_body_with_query() -> None:
    body = build_document_search_body(query="liability", filters=[], from_=0, size=10)
    must = body["query"]["bool"]["must"][0]
    assert "should" in must["bool"]
    assert body["size"] == 10


def test_build_document_search_body_empty_query_matches_all() -> None:
    body = build_document_search_body(query=None, filters=[], from_=0, size=5)
    assert body["query"]["bool"]["must"] == [{"match_all": {}}]


def test_build_hybrid_query_structure() -> None:
    body = build_hybrid_query(query_text="hello", query_vector=[0.1, 0.2], top_k=5)
    assert body["size"] == 5
    queries = body["query"]["hybrid"]["queries"]
    assert len(queries) == 2
    # The lexical clause matches snippet and title.
    assert queries[0]["multi_match"]["fields"] == ["snippet", "title^2"]
    assert body["_source"]["excludes"] == ["embedding"]


def test_build_hybrid_query_with_filters_applies_to_both_clauses() -> None:
    filters = build_filters(doc_type="invoice")
    body = build_hybrid_query(query_text="hello", query_vector=[0.1], top_k=3, filters=filters)
    match_clause, knn_clause = body["query"]["hybrid"]["queries"]
    assert "filter" in match_clause["bool"]
    assert match_clause["bool"]["must"][0]["multi_match"]["fields"] == ["snippet", "title^2"]
    assert "filter" in knn_clause["knn"]["embedding"]
