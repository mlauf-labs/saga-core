"""Unit tests for the OpenSearch mapping and query builders (search projection)."""

from __future__ import annotations

from saga.core.config import OpenSearchConfig
from saga.core.models import ExtractedValue
from saga.storage.mappings import (
    build_document_filters,
    build_document_search_body,
    build_filters,
    build_folder_filter,
    build_keyword_query_body,
    build_metadata_text,
    build_more_like_this_body,
    build_semantic_query_body,
    build_summary_knn_body,
    build_value_terms,
    chunk_index_body,
    document_index_body,
)

# --------------------------------------------------------------------------- #
# Index bodies                                                                  #
# --------------------------------------------------------------------------- #


def test_document_index_body_has_projection_fields() -> None:
    props = document_index_body(OpenSearchConfig())["mappings"]["properties"]
    assert props["document_id"]["type"] == "keyword"
    assert props["content_markdown"]["type"] == "text"
    assert props["extracted_values"]["type"] == "nested"
    # New folder-based projection fields replace the old category_paths.
    assert props["folder_ids"]["type"] == "keyword"
    assert props["folder_ancestor_ids"]["type"] == "keyword"
    assert props["primary_folder_id"]["type"] == "keyword"
    assert props["value_terms"]["type"] == "keyword"
    assert "category_paths" not in props


def test_document_index_body_summary_embedding_is_knn_vector() -> None:
    cfg = OpenSearchConfig(vector_dimension=512, vector_engine="lucene")
    body = document_index_body(cfg)
    assert body["settings"]["index"]["knn"] is True
    embedding = body["mappings"]["properties"]["summary_embedding"]
    assert embedding["type"] == "knn_vector"
    assert embedding["dimension"] == 512
    assert embedding["method"]["engine"] == "lucene"


def test_chunk_index_body_uses_config_vector_params() -> None:
    cfg = OpenSearchConfig(
        vector_dimension=1024,
        vector_engine="lucene",
        vector_space_type="l2",
        knn_ef_construction=128,
        knn_m=8,
    )
    body = chunk_index_body(cfg)
    assert body["settings"]["index"]["knn"] is True
    embedding = body["mappings"]["properties"]["embedding"]
    assert embedding["type"] == "knn_vector"
    assert embedding["dimension"] == 1024
    assert embedding["method"]["engine"] == "lucene"
    assert embedding["method"]["space_type"] == "l2"
    assert embedding["method"]["parameters"]["ef_construction"] == 128
    assert embedding["method"]["parameters"]["m"] == 8


def test_chunk_index_body_has_denormalised_metadata() -> None:
    props = chunk_index_body(OpenSearchConfig())["mappings"]["properties"]
    assert props["title"]["type"] == "text"
    assert props["title"]["fields"]["keyword"]["type"] == "keyword"
    assert props["status"]["type"] == "keyword"
    assert props["created_at"]["type"] == "date"
    assert props["mime_type"]["type"] == "keyword"
    assert props["size_bytes"]["type"] == "long"
    # Folder denormalisation replaces the old folder_structure/category_paths.
    assert props["folder_ids"]["type"] == "keyword"
    assert props["folder_ancestor_ids"]["type"] == "keyword"
    assert props["value_terms"]["type"] == "keyword"
    assert "category_paths" not in props
    assert "folder_structure" not in props


# --------------------------------------------------------------------------- #
# Value terms                                                                   #
# --------------------------------------------------------------------------- #


def test_build_value_terms_includes_raw_and_normalized() -> None:
    values = [
        ExtractedValue(key="amount", type="amount", value="100,00", normalized="100.00"),
        ExtractedValue(key="iban", type="iban", value="DE123"),
        ExtractedValue(key="ref", type="identifier", value="A1", normalized="A1"),
    ]
    terms = build_value_terms(values)
    assert "amount=100,00" in terms
    assert "amount=100.00" in terms
    assert "iban=DE123" in terms
    # When the normalized value equals the raw value, it is not duplicated.
    assert terms.count("ref=A1") == 1


def test_build_value_terms_empty() -> None:
    assert build_value_terms([]) == []


# --------------------------------------------------------------------------- #
# Metadata projection (nested + flattened text)                                 #
# --------------------------------------------------------------------------- #


def test_metadata_text_flattens_key_values() -> None:
    assert build_metadata_text({"project": "Apollo", "rank": "1"}) == "project: Apollo\nrank: 1"
    assert build_metadata_text({}) == ""


def test_document_index_body_has_metadata_fields() -> None:
    props = document_index_body(OpenSearchConfig())["mappings"]["properties"]
    assert props["metadata"]["type"] == "nested"
    assert props["metadata"]["properties"]["key"]["type"] == "keyword"
    assert props["metadata"]["properties"]["value"]["type"] == "text"
    assert props["metadata"]["properties"]["value"]["fields"]["keyword"]["type"] == "keyword"
    assert props["metadata_text"]["type"] == "text"


# --------------------------------------------------------------------------- #
# Folder filter                                                                 #
# --------------------------------------------------------------------------- #


def test_build_folder_filter_subtree_uses_ancestor_field() -> None:
    assert build_folder_filter("f1") == {"term": {"folder_ancestor_ids": "f1"}}
    assert build_folder_filter("f1", include_subtree=True) == {
        "term": {"folder_ancestor_ids": "f1"}
    }


def test_build_folder_filter_exact_uses_folder_ids() -> None:
    assert build_folder_filter("f1", include_subtree=False) == {"term": {"folder_ids": "f1"}}


# --------------------------------------------------------------------------- #
# Chunk filters                                                                 #
# --------------------------------------------------------------------------- #


def test_build_filters_all_clauses() -> None:
    filters = build_filters(
        doc_type="invoice",
        folder_id="f1",
        title="Invoice 1",
        status="ready",
        created_from="2024-01-01",
        created_to="2024-12-31",
        extracted_values={"invoice_number": "123"},
    )
    assert {"term": {"doc_type": "invoice"}} in filters
    assert {"term": {"title.keyword": "Invoice 1"}} in filters
    assert {"term": {"status": "ready"}} in filters
    assert {"term": {"folder_ancestor_ids": "f1"}} in filters
    assert {"range": {"created_at": {"gte": "2024-01-01", "lte": "2024-12-31"}}} in filters
    assert {"term": {"value_terms": "invoice_number=123"}} in filters


def test_build_filters_exact_folder() -> None:
    filters = build_filters(folder_id="f1", include_subtree=False)
    assert filters == [{"term": {"folder_ids": "f1"}}]


def test_build_filters_empty_is_empty_list() -> None:
    assert build_filters() == []


def test_build_filters_date_range_open_ended() -> None:
    filters = build_filters(created_from="2024-01-01")
    assert filters == [{"range": {"created_at": {"gte": "2024-01-01"}}}]


# --------------------------------------------------------------------------- #
# Document filters (nested extracted_values)                                    #
# --------------------------------------------------------------------------- #


def test_build_document_filters_simple_clauses() -> None:
    filters = build_document_filters(
        doc_type="invoice",
        folder_id="f1",
        title="t",
        status="ready",
    )
    assert {"term": {"doc_type": "invoice"}} in filters
    assert {"term": {"title.keyword": "t"}} in filters
    assert {"term": {"status": "ready"}} in filters
    assert {"term": {"folder_ancestor_ids": "f1"}} in filters


def test_build_document_filters_nested_extracted_values() -> None:
    filters = build_document_filters(extracted_values={"invoice_number": "123"})
    nested = [f for f in filters if "nested" in f]
    assert len(nested) == 1
    query = nested[0]["nested"]
    assert query["path"] == "extracted_values"
    clauses = query["query"]["bool"]["filter"]
    assert {"term": {"extracted_values.key": "invoice_number"}} in clauses
    assert {"term": {"extracted_values.value.keyword": "123"}} in clauses


# --------------------------------------------------------------------------- #
# Document search body                                                          #
# --------------------------------------------------------------------------- #


def test_build_document_search_body_with_query() -> None:
    body = build_document_search_body(
        query="liability", filters=[{"term": {"doc_type": "contract"}}], from_=10, size=20
    )
    assert body["from"] == 10
    assert body["size"] == 20
    must = body["query"]["bool"]["must"][0]
    should = must["bool"]["should"]
    assert any("multi_match" in clause for clause in should)
    assert any("nested" in clause for clause in should)
    assert {"term": {"doc_type": "contract"}} in body["query"]["bool"]["filter"]
    assert body["_source"]["excludes"] == ["summary_embedding"]


def test_build_document_search_body_empty_query_matches_all() -> None:
    body = build_document_search_body(query=None, filters=[], from_=0, size=5)
    assert body["query"]["bool"]["must"] == [{"match_all": {}}]


# --------------------------------------------------------------------------- #
# Keyword query body                                                            #
# --------------------------------------------------------------------------- #


def test_build_keyword_query_body() -> None:
    body = build_keyword_query_body(
        query="title:Rechnung AND 2024",
        fields=["title^3", "content_markdown"],
        default_operator="AND",
        filters=[{"term": {"doc_type": "invoice"}}],
        size=7,
    )
    assert body["size"] == 7
    qs = body["query"]["bool"]["must"][0]["query_string"]
    assert qs["query"] == "title:Rechnung AND 2024"
    assert qs["fields"] == ["title^3", "content_markdown"]
    assert qs["default_operator"] == "AND"
    assert qs["lenient"] is True
    assert {"term": {"doc_type": "invoice"}} in body["query"]["bool"]["filter"]
    assert "content_markdown" in body["highlight"]["fields"]
    assert body["_source"]["excludes"] == ["summary_embedding"]


# --------------------------------------------------------------------------- #
# Semantic (chunk kNN) query body                                               #
# --------------------------------------------------------------------------- #


def test_build_semantic_query_body() -> None:
    body = build_semantic_query_body(query_vector=[0.1, 0.2], top_k=4)
    assert body["size"] == 4
    knn = body["query"]["knn"]["embedding"]
    assert knn["vector"] == [0.1, 0.2]
    assert knn["k"] == 4
    assert "filter" not in knn
    assert body["_source"]["excludes"] == ["embedding"]


def test_build_semantic_query_body_with_filters() -> None:
    body = build_semantic_query_body(
        query_vector=[0.1], top_k=3, filters=[{"term": {"doc_type": "invoice"}}]
    )
    knn_filter = body["query"]["knn"]["embedding"]["filter"]
    assert {"term": {"doc_type": "invoice"}} in knn_filter["bool"]["filter"]


# --------------------------------------------------------------------------- #
# Summary kNN similarity body                                                   #
# --------------------------------------------------------------------------- #


def test_build_summary_knn_body() -> None:
    body = build_summary_knn_body(query_vector=[0.1, 0.2, 0.3], top_k=6)
    assert body["size"] == 6
    knn = body["query"]["knn"]["summary_embedding"]
    assert knn["vector"] == [0.1, 0.2, 0.3]
    assert knn["k"] == 6
    assert "filter" not in knn
    assert "summary_embedding" in body["_source"]["excludes"]


def test_build_summary_knn_body_excludes_document() -> None:
    body = build_summary_knn_body(query_vector=[0.1], top_k=5, exclude_document_id="d1")
    must_not = body["query"]["knn"]["summary_embedding"]["filter"]["bool"]["must_not"]
    assert {"term": {"document_id": "d1"}} in must_not


# --------------------------------------------------------------------------- #
# more_like_this lexical similarity body                                        #
# --------------------------------------------------------------------------- #


def test_build_more_like_this_body() -> None:
    body = build_more_like_this_body(text="liability clauses", top_k=8)
    assert body["size"] == 8
    mlt = body["query"]["bool"]["must"][0]["more_like_this"]
    assert mlt["like"] == "liability clauses"
    assert "title" in mlt["fields"]
    assert body["query"]["bool"]["must_not"] == []
    assert "content_markdown" in body["_source"]["excludes"]


def test_build_more_like_this_body_excludes_document() -> None:
    body = build_more_like_this_body(text="x", top_k=3, exclude_document_id="d9")
    assert {"term": {"document_id": "d9"}} in body["query"]["bool"]["must_not"]
