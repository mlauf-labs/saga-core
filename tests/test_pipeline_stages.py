"""Unit tests for the ingestion-pipeline stages (FR-4 ff.).

Stages that persist run against the real :class:`PostgresStore` (the ``db`` fixture,
in-memory SQLite). External services (converter, MinIO, analyzer, OpenSearch,
chunker, embedder) are replaced by small inline fakes so no network is required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from saga.core.errors import NotFoundError
from saga.core.models import DocumentStatus, Event, EventCategory, EventType, ExtractedValue
from saga.llm.schemas import (
    DocTypeAssignment,
    ExtractedValueOut,
    FolderDecision,
    FolderPlacement,
    NewFolder,
    Summary,
    TimelineEventOut,
    TimelineExtraction,
    ValueExtraction,
)
from saga.pipeline.stages import (
    classify_doc_type,
    convert_to_markdown,
    extract_timeline,
    extract_values,
    index_chunks,
    place_in_folder,
    summarize,
)
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeEmbedder, seed_document

# --------------------------------------------------------------------------- #
# Inline fakes                                                                  #
# --------------------------------------------------------------------------- #


class _FakeConverter:
    name = "fake"

    def __init__(self, markdown: str) -> None:
        self._markdown = markdown

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        return self._markdown


class _FakeConverterRegistry:
    def __init__(self, converter: _FakeConverter) -> None:
        self._converter = converter
        self.resolved: dict[str, str] = {}

    def resolve(self, *, filename: str, mime_type: str) -> _FakeConverter:
        self.resolved = {"filename": filename, "mime_type": mime_type}
        return self._converter


class _FakeMinio:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    async def get_object(self, object_name: str) -> bytes:
        return self.objects.get(object_name, b"%PDF-binary")


class _FakeChunker:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def split(self, content: str) -> list[str]:
        return list(self._texts)


class _FakeOpenSearch:
    """Records projection + chunk-indexing instead of talking to OpenSearch."""

    def __init__(self) -> None:
        self.projected: dict[str, dict[str, Any]] = {}
        self.deleted_chunks: list[str] = []
        self.indexed_chunks: list[Any] = []

    async def project_document(
        self,
        document: Any,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        self.projected[document.document_id] = {
            "document": document,
            "folder_ancestor_ids": folder_ancestor_ids,
            "summary_embedding": summary_embedding,
        }

    async def delete_chunks(self, document_id: str) -> None:
        self.deleted_chunks.append(document_id)

    async def index_chunks(self, chunks: list[Any]) -> int:
        self.indexed_chunks = list(chunks)
        return len(chunks)


class _FakeAnalyzer:
    """Returns canned schema objects and records the kwargs it was called with."""

    def __init__(
        self,
        *,
        doc_type: DocTypeAssignment | None = None,
        values: ValueExtraction | None = None,
        summary: Summary | None = None,
        placement: FolderPlacement | None = None,
        timeline: TimelineExtraction | None = None,
    ) -> None:
        self._doc_type = doc_type
        self._values = values
        self._summary = summary
        self._placement = placement
        self._timeline = timeline
        self.calls: dict[str, dict[str, Any]] = {}

    async def classify_doc_type(self, **kwargs: Any) -> DocTypeAssignment | None:
        self.calls["classify_doc_type"] = kwargs
        return self._doc_type

    async def extract_values(self, **kwargs: Any) -> ValueExtraction | None:
        self.calls["extract_values"] = kwargs
        return self._values

    async def extract_timeline(self, **kwargs: Any) -> TimelineExtraction | None:
        self.calls["extract_timeline"] = kwargs
        return self._timeline

    async def summarize(self, **kwargs: Any) -> Summary | None:
        self.calls["summarize"] = kwargs
        return self._summary

    async def place_in_folder(self, **kwargs: Any) -> FolderPlacement | None:
        self.calls["place_in_folder"] = kwargs
        return self._placement

    async def place_in_folder_agentic(self, **kwargs: Any) -> FolderDecision | None:
        # Exercise the legacy fallback: return None so place_in_folder runs.
        self.calls["place_in_folder_agentic"] = kwargs
        return None


# --------------------------------------------------------------------------- #
# convert_to_markdown                                                           #
# --------------------------------------------------------------------------- #


async def test_convert_to_markdown_persists_content(db: PostgresStore) -> None:
    doc = await seed_document(db, title="invoice.pdf", status=DocumentStatus.PENDING)
    minio = _FakeMinio({doc.document_id: b"%PDF-binary"})
    converters = _FakeConverterRegistry(_FakeConverter("# Converted markdown"))

    result = await convert_to_markdown(
        document_id=doc.document_id,
        db=db,
        minio=minio,  # type: ignore[arg-type]
        converters=converters,  # type: ignore[arg-type]
    )

    assert result == "# Converted markdown"
    assert converters.resolved == {"filename": "invoice.pdf", "mime_type": "application/pdf"}
    stored = await db.get_document(doc.document_id)
    assert stored is not None
    assert stored.content_markdown == "# Converted markdown"


async def test_convert_to_markdown_missing_document_raises(db: PostgresStore) -> None:
    minio = _FakeMinio()
    converters = _FakeConverterRegistry(_FakeConverter("x"))
    with pytest.raises(NotFoundError):
        await convert_to_markdown(
            document_id="missing",
            db=db,
            minio=minio,  # type: ignore[arg-type]
            converters=converters,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# classify_doc_type                                                             #
# --------------------------------------------------------------------------- #


async def test_classify_doc_type_creates_and_assigns(db: PostgresStore) -> None:
    doc = await seed_document(db, title="inv.pdf")
    analyzer = _FakeAnalyzer(
        doc_type=DocTypeAssignment(doc_type="invoice", is_new=True, description="a bill")
    )

    result = await classify_doc_type(
        document_id=doc.document_id,
        title="inv.pdf",
        markdown="# md",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=True,
    )

    assert result is not None
    assert result.name == "invoice"
    stored = await db.get_document(doc.document_id)
    assert stored is not None
    assert stored.doc_type == "invoice"
    assert {dt.name for dt in await db.list_doc_types()} == {"invoice"}


async def test_classify_doc_type_reuses_existing(db: PostgresStore) -> None:
    doc = await seed_document(db)
    existing = await db.ensure_doc_type(name="contract", description="legal")
    analyzer = _FakeAnalyzer(doc_type=DocTypeAssignment(doc_type="contract"))

    result = await classify_doc_type(
        document_id=doc.document_id,
        title="c.pdf",
        markdown="# md",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=True,
    )

    assert result is not None
    assert result.doc_type_id == existing.doc_type_id
    # No duplicate doc-type was created.
    assert len(await db.list_doc_types()) == 1


async def test_classify_doc_type_no_create_when_disabled(db: PostgresStore) -> None:
    doc = await seed_document(db)
    analyzer = _FakeAnalyzer(doc_type=DocTypeAssignment(doc_type="invoice", is_new=True))

    result = await classify_doc_type(
        document_id=doc.document_id,
        title="inv.pdf",
        markdown="# md",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=False,
    )

    assert result is None
    assert await db.list_doc_types() == []


async def test_classify_doc_type_none_assignment_returns_none(db: PostgresStore) -> None:
    doc = await seed_document(db)
    analyzer = _FakeAnalyzer(doc_type=None)
    result = await classify_doc_type(
        document_id=doc.document_id,
        title="x",
        markdown="y",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=True,
    )
    assert result is None


# --------------------------------------------------------------------------- #
# extract_values                                                                #
# --------------------------------------------------------------------------- #


async def test_extract_values_persists_keyed_values(db: PostgresStore) -> None:
    doc = await seed_document(db)
    analyzer = _FakeAnalyzer(
        values=ValueExtraction(
            values=[
                ExtractedValueOut(key="invoice_number", type="identifier", value="INV-1"),
                # Values without a key are dropped by the stage (empty key + type).
                ExtractedValueOut(key="", type="", value="noise"),
            ]
        )
    )

    values = await extract_values(
        document_id=doc.document_id,
        markdown="# md",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
    )

    assert [v.key for v in values] == ["invoice_number"]
    stored = await db.get_document(doc.document_id)
    assert stored is not None
    assert [v.value for v in stored.extracted_values] == ["INV-1"]


async def test_extract_values_handles_none(db: PostgresStore) -> None:
    doc = await seed_document(db)
    analyzer = _FakeAnalyzer(values=None)
    values = await extract_values(
        document_id=doc.document_id,
        markdown="# md",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
    )
    assert values == []


# --------------------------------------------------------------------------- #
# summarize                                                                     #
# --------------------------------------------------------------------------- #


async def test_summarize_persists_summary_and_embedding(db: PostgresStore) -> None:
    doc = await seed_document(db, title="report.pdf")
    analyzer = _FakeAnalyzer(
        summary=Summary(title="Quarterly Report", summary="A quarterly report.")
    )
    embedder = FakeEmbedder(dimension=8)

    summary, title, vector = await summarize(
        document_id=doc.document_id,
        filename="report.pdf",
        markdown="# long body",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
    )

    assert summary == "A quarterly report."
    assert title == "Quarterly Report"
    assert len(vector) == 8
    stored = await db.get_document(doc.document_id)
    assert stored is not None
    assert stored.summary == "A quarterly report."
    assert stored.title == "Quarterly Report"
    assert await db.get_summary_embedding(doc.document_id) == vector


async def test_summarize_falls_back_to_filename(db: PostgresStore) -> None:
    doc = await seed_document(db, title="fallback.pdf")
    analyzer = _FakeAnalyzer(summary=None)
    embedder = FakeEmbedder(dimension=4)

    summary, title, _ = await summarize(
        document_id=doc.document_id,
        filename="fallback.pdf",
        markdown="# body",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
    )

    assert summary == "fallback.pdf"
    assert title == "fallback.pdf"


# --------------------------------------------------------------------------- #
# place_in_folder                                                               #
# --------------------------------------------------------------------------- #


async def test_place_in_folder_assigns_existing_folder(db: PostgresStore) -> None:
    doc = await seed_document(db)
    folder = await db.create_folder(name="Finance", description="money")
    analyzer = _FakeAnalyzer(
        placement=FolderPlacement(assignments=[folder.folder_id], primary=folder.folder_id)
    )

    refs = await place_in_folder(
        document_id=doc.document_id,
        summary="A finance document.",
        doc_type="invoice",
        extracted_values=[ExtractedValue(key="amount", type="amount", value="5")],
        votes=[],
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=True,
    )

    assert [r.folder_id for r in refs] == [folder.folder_id]
    assert refs[0].is_primary is True
    membership = await db.get_document_folders(doc.document_id)
    assert [m.folder_id for m in membership] == [folder.folder_id]
    # The placement prompt received the rendered folder tree + values.
    call = analyzer.calls["place_in_folder"]
    assert folder.folder_id in call["folder_tree"]
    assert "amount=5" in call["extracted_values"]


async def test_place_in_folder_creates_new_folder(db: PostgresStore) -> None:
    doc = await seed_document(db)
    analyzer = _FakeAnalyzer(
        placement=FolderPlacement(
            new_folders=[NewFolder(name="Taxes", description="tax docs")],
            new_folder_primary="Taxes",
        )
    )

    refs = await place_in_folder(
        document_id=doc.document_id,
        summary="A tax document.",
        doc_type="tax_document",
        extracted_values=[],
        votes=[],
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=True,
    )

    assert len(refs) == 1
    folders = await db.list_folders()
    assert {f.name for f in folders} == {"Taxes"}
    taxes = folders[0]
    assert refs[0].folder_id == taxes.folder_id
    assert refs[0].is_primary is True


async def test_place_in_folder_empty_returns_no_refs(db: PostgresStore) -> None:
    doc = await seed_document(db)
    analyzer = _FakeAnalyzer(placement=FolderPlacement())

    refs = await place_in_folder(
        document_id=doc.document_id,
        summary="s",
        doc_type=None,
        extracted_values=[],
        votes=[],
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        allow_auto_create=True,
    )

    assert refs == []
    assert await db.get_document_folders(doc.document_id) == []


# --------------------------------------------------------------------------- #
# index_chunks                                                                  #
# --------------------------------------------------------------------------- #


async def test_index_chunks_projects_and_indexes(db: PostgresStore) -> None:
    doc = await seed_document(db, title="doc.pdf", content="some markdown body")
    opensearch = _FakeOpenSearch()
    chunker = _FakeChunker(["chunk one", "chunk two"])
    embedder = FakeEmbedder(dimension=8)

    indexed = await index_chunks(
        document_id=doc.document_id,
        summary_vector=[0.5, 0.0],
        db=db,
        opensearch=opensearch,  # type: ignore[arg-type]
        chunker=chunker,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
    )

    assert indexed == 2
    assert doc.document_id in opensearch.projected
    assert opensearch.projected[doc.document_id]["summary_embedding"] == [0.5, 0.0]
    assert opensearch.deleted_chunks == [doc.document_id]
    assert [c.chunk_id for c in opensearch.indexed_chunks] == [
        f"{doc.document_id}:0",
        f"{doc.document_id}:1",
    ]
    assert opensearch.indexed_chunks[0].title == "doc.pdf"


async def test_index_chunks_no_chunks_returns_zero(db: PostgresStore) -> None:
    doc = await seed_document(db, content="")
    opensearch = _FakeOpenSearch()
    chunker = _FakeChunker([])
    embedder = FakeEmbedder()

    indexed = await index_chunks(
        document_id=doc.document_id,
        summary_vector=[],
        db=db,
        opensearch=opensearch,  # type: ignore[arg-type]
        chunker=chunker,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
    )

    assert indexed == 0
    assert opensearch.indexed_chunks == []
    # Projection still happens before chunking.
    assert doc.document_id in opensearch.projected


async def test_index_chunks_missing_document_raises(db: PostgresStore) -> None:
    opensearch = _FakeOpenSearch()
    with pytest.raises(NotFoundError):
        await index_chunks(
            document_id="missing",
            summary_vector=[],
            db=db,
            opensearch=opensearch,  # type: ignore[arg-type]
            chunker=_FakeChunker([]),  # type: ignore[arg-type]
            embedder=FakeEmbedder(),  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# extract_timeline                                                              #
# --------------------------------------------------------------------------- #


def _make_content_event(document_id: str) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id="",
        category=EventCategory.CONTENT,
        event_type=EventType.DATED_FACT,
        document_id=document_id,
        occurred_at=now,
        recorded_at=now,
        actor="extraction",
        summary="prior",
    )


async def test_extract_timeline_persists_filtered_content_events(db: PostgresStore) -> None:
    analyzer = _FakeAnalyzer(
        timeline=TimelineExtraction(
            events=[
                TimelineEventOut(
                    kind="future", description="Policy expiry", date="2027-04-30", confidence=0.9
                ),
                TimelineEventOut(kind="past", description="No date", date="", confidence=0.9),
                TimelineEventOut(
                    kind="past", description="Low conf", date="2026-01-01", confidence=0.1
                ),
                TimelineEventOut(
                    kind="recurring",
                    description="Annual renewal",
                    date="2026-05-01",
                    end_date="2030-05-01",
                    recurrence="FREQ=YEARLY",
                    confidence=0.8,
                ),
            ]
        )
    )
    count = await extract_timeline(
        document_id="d1",
        markdown="text",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        min_confidence=0.5,
    )
    assert count == 2  # dateless + low-confidence dropped
    events = await db.query_events(categories=[EventCategory.CONTENT], document_id="d1")
    types = {e.event_type for e in events}
    assert types == {EventType.APPOINTMENT, EventType.RECURRING}
    recurring = next(e for e in events if e.event_type == EventType.RECURRING)
    assert recurring.details["recurrence"] == "FREQ=YEARLY"
    assert recurring.details["end_date"] == "2030-05-01"
    assert recurring.summary == "Annual renewal"


async def test_extract_timeline_keeps_prior_events_on_failure(db: PostgresStore) -> None:
    await db.replace_content_events("d9", [_make_content_event("d9")])
    analyzer = _FakeAnalyzer(timeline=None)
    count = await extract_timeline(
        document_id="d9",
        markdown="x",
        db=db,
        analyzer=analyzer,  # type: ignore[arg-type]
        min_confidence=0.5,
    )
    assert count == 0
    events = await db.query_events(categories=[EventCategory.CONTENT], document_id="d9")
    assert len(events) == 1  # prior kept, not wiped
