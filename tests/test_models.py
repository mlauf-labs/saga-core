"""Unit tests for the domain models."""

from __future__ import annotations

from datetime import UTC, datetime

from docstore.core.models import (
    CategoryNode,
    Chunk,
    Document,
    DocumentStatus,
    ExtractedValue,
    SearchHit,
)


def test_document_defaults() -> None:
    now = datetime.now(UTC)
    doc = Document(
        document_id="d1",
        title="Invoice.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        content_hash="abc",
        minio_object="docstore-originals/d1",
        created_at=now,
        updated_at=now,
    )
    assert doc.status is DocumentStatus.PENDING
    assert doc.extracted_values == []
    assert doc.folder_structure == []


def test_extracted_value_and_chunk() -> None:
    value = ExtractedValue(key="invoice_number", type="identifier", value="12345")
    assert value.confidence == 1.0
    chunk = Chunk(
        chunk_id="d1:0",
        document_id="d1",
        ordinal=0,
        snippet="text",
        embedding=[0.1, 0.2],
    )
    assert chunk.document_id == "d1"


def test_search_hit_and_category_tree() -> None:
    hit = SearchHit(document_id="d1", chunk_id="d1:0", snippet="s", score=0.9, title="Invoice.pdf")
    assert hit.score == 0.9
    root = CategoryNode(
        path="Insurance",
        name="Insurance",
        document_count=2,
        children=[CategoryNode(path="Insurance/Health", name="Health", document_count=1)],
    )
    assert root.children[0].name == "Health"
