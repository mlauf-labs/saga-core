"""Unit tests for the domain models (folders, doc-types, notes, search results)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from saga.core.models import (
    Chunk,
    DocType,
    Document,
    DocumentStatus,
    EventType,
    ExtractedValue,
    Folder,
    FolderNode,
    FolderRef,
    HybridSearchResult,
    Note,
    SearchHit,
    SearchResultItem,
)


def _now() -> datetime:
    return datetime.now(UTC)


def test_event_type_has_deletion_audit_values() -> None:
    assert EventType.DOCUMENT_DELETED.value == "document_deleted"
    assert EventType.FOLDER_DELETED.value == "folder_deleted"
    assert EventType.DOC_TYPE_DELETED.value == "doc_type_deleted"


# --------------------------------------------------------------------------- #
# Document                                                                      #
# --------------------------------------------------------------------------- #


def test_document_defaults() -> None:
    now = _now()
    doc = Document(
        document_id="d1",
        title="Invoice.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        content_hash="abc",
        minio_object="saga-originals/d1",
        created_at=now,
        updated_at=now,
    )
    assert doc.status is DocumentStatus.PENDING
    assert doc.extracted_values == []
    assert doc.folders == []
    assert doc.notes == []
    # No folders -> no derived ids.
    assert doc.folder_ids == []
    assert doc.primary_folder_id is None


def test_document_folder_ids_preserve_order() -> None:
    now = _now()
    doc = Document(
        document_id="d1",
        title="t",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="o",
        created_at=now,
        updated_at=now,
        folders=[
            FolderRef(folder_id="f1", name="One"),
            FolderRef(folder_id="f2", name="Two"),
            FolderRef(folder_id="f3", name="Three"),
        ],
    )
    assert doc.folder_ids == ["f1", "f2", "f3"]


def test_primary_folder_id_prefers_is_primary() -> None:
    now = _now()
    doc = Document(
        document_id="d1",
        title="t",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="o",
        created_at=now,
        updated_at=now,
        folders=[
            FolderRef(folder_id="f1", name="One"),
            FolderRef(folder_id="f2", name="Two", is_primary=True),
        ],
    )
    assert doc.primary_folder_id == "f2"


def test_primary_folder_id_falls_back_to_first() -> None:
    now = _now()
    doc = Document(
        document_id="d1",
        title="t",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="o",
        created_at=now,
        updated_at=now,
        folders=[
            FolderRef(folder_id="f1", name="One"),
            FolderRef(folder_id="f2", name="Two"),
        ],
    )
    # No folder is marked primary, so the first one wins.
    assert doc.primary_folder_id == "f1"


# --------------------------------------------------------------------------- #
# DocumentStatus                                                                #
# --------------------------------------------------------------------------- #


def test_document_status_members_and_values() -> None:
    expected = {
        "PENDING": "pending",
        "CONVERTING": "converting",
        "CLASSIFYING_TYPE": "classifying_type",
        "ANALYZING": "analyzing",
        "SUMMARIZING": "summarizing",
        "CLASSIFYING": "classifying",
        "INDEXING": "indexing",
        "READY": "ready",
        "FAILED": "failed",
    }
    assert {member.name: member.value for member in DocumentStatus} == expected
    # StrEnum members carry their string value.
    assert DocumentStatus.READY.value == "ready"
    assert str(DocumentStatus.READY) == "ready"


# --------------------------------------------------------------------------- #
# ExtractedValue + Chunk                                                        #
# --------------------------------------------------------------------------- #


def test_extracted_value_defaults() -> None:
    value = ExtractedValue(key="invoice_number", type="identifier", value="12345")
    assert value.confidence == 1.0
    assert value.normalized is None


def test_chunk_round_trip() -> None:
    chunk = Chunk(
        chunk_id="d1:0",
        document_id="d1",
        ordinal=0,
        snippet="text",
        embedding=[0.1, 0.2],
    )
    assert chunk.document_id == "d1"
    assert chunk.folder_ids == []
    assert chunk.title == ""
    restored = Chunk.model_validate(chunk.model_dump())
    assert restored == chunk


# --------------------------------------------------------------------------- #
# Folders / doc-types / notes                                                   #
# --------------------------------------------------------------------------- #


def test_folder_ref_defaults() -> None:
    ref = FolderRef(folder_id="f1", name="Finance")
    assert ref.is_primary is False


def test_folder_round_trip() -> None:
    now = _now()
    folder = Folder(
        folder_id="finance",
        name="Finance",
        description="Money things",
        parent_id=None,
        metadata={"color": "green"},
        notes=[
            Note(note_id="n1", content="keep tidy", created_at=now, updated_at=now),
        ],
        created_at=now,
        updated_at=now,
    )
    restored = Folder.model_validate(folder.model_dump())
    assert restored == folder
    assert restored.metadata == {"color": "green"}
    assert restored.notes[0].content == "keep tidy"


def test_folder_defaults() -> None:
    now = _now()
    folder = Folder(folder_id="f1", name="Top", created_at=now, updated_at=now)
    assert folder.parent_id is None
    assert folder.metadata == {}
    assert folder.notes == []


def test_folder_node_nesting_and_counts() -> None:
    node = FolderNode(
        folder_id="finance",
        name="Finance",
        document_count=5,
        children=[FolderNode(folder_id="finance/invoices", name="Invoices", document_count=2)],
    )
    assert node.document_count == 5
    assert node.children[0].name == "Invoices"
    assert node.children[0].children == []


def test_doc_type_round_trip() -> None:
    now = _now()
    doc_type = DocType(
        doc_type_id="invoice",
        name="Invoice",
        description="A bill",
        document_count=7,
        created_at=now,
        updated_at=now,
    )
    restored = DocType.model_validate(doc_type.model_dump())
    assert restored == doc_type
    assert restored.document_count == 7


def test_note_requires_timestamps() -> None:
    now = _now()
    note = Note(note_id="n1", content="hello", created_at=now, updated_at=now)
    assert note.content == "hello"
    with pytest.raises(ValidationError):
        Note(note_id="n2", content="missing timestamps")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Search result models                                                          #
# --------------------------------------------------------------------------- #


def test_search_hit_defaults() -> None:
    hit = SearchHit(document_id="d1", chunk_id="d1:0", snippet="s", score=0.9, title="Invoice.pdf")
    assert hit.score == 0.9
    assert hit.folder_ids == []
    assert hit.doc_type is None


def test_search_result_item_round_trip() -> None:
    item = SearchResultItem(
        document_id="d1",
        title="Invoice.pdf",
        score=1.5,
        doc_type="invoice",
        summary="A bill",
        folder_ids=["finance"],
        snippet="match",
    )
    restored = SearchResultItem.model_validate(item.model_dump())
    assert restored == item


def test_hybrid_search_result_holds_single_fused_list() -> None:
    empty = HybridSearchResult()
    assert empty.results == []
    populated = HybridSearchResult(
        results=[SearchResultItem(document_id="d1", title="t", score=1.0)]
    )
    assert len(populated.results) == 1
    assert populated.results[0].document_id == "d1"
