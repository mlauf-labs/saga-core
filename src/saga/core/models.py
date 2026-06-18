"""Typed domain models shared across the application (NFR-2).

This module intentionally does NOT use ``from __future__ import annotations`` so
that Pydantic can resolve runtime types (e.g. ``datetime``) eagerly.

Postgres is the system of record for documents, folders, doc-types, notes and the
document<->folder membership. OpenSearch holds a derived, rebuildable search
projection (full text + chunk vectors + denormalised filter fields).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class DocumentStatus(StrEnum):
    """Lifecycle status of a document (FR-12).

    The ingestion pipeline walks these in order: ``pending`` -> ``converting`` ->
    ``classifying_type`` (doc-type) -> ``analyzing`` (value extraction) ->
    ``summarizing`` -> ``classifying`` (similarity + folder placement) ->
    ``indexing`` -> ``ready`` (or ``failed``).
    """

    PENDING = "pending"
    CONVERTING = "converting"
    CLASSIFYING_TYPE = "classifying_type"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    CLASSIFYING = "classifying"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class EventCategory(StrEnum):
    """Which view an event belongs to (see timeline design)."""

    AUDIT = "audit"
    CONTENT = "content"


class EventType(StrEnum):
    """The kind of event. Audit types are emitted by code; content types by the LLM."""

    # audit (Phase 1)
    DOC_INGESTED = "doc_ingested"
    PLACEMENT = "placement"
    MOVE = "move"
    RECLASSIFICATION = "reclassification"
    FOLDER_CREATED = "folder_created"
    FOLDER_RENAMED = "folder_renamed"
    # content (Phase 2/3)
    DATED_FACT = "dated_fact"
    APPOINTMENT = "appointment"
    RECURRING = "recurring"


class Event(BaseModel):
    """A single timeline event (system of record: Postgres ``events`` table).

    ``occurred_at`` is the event time (content: real-world date; audit: equals
    ``recorded_at``). ``recorded_at`` is the archive time the row was written.
    ``dedupe_key`` makes re-emitted identical audit events a no-op.
    """

    event_id: str
    category: EventCategory
    event_type: EventType
    document_id: str | None = None
    folder_id: str | None = None
    occurred_at: datetime | None = None
    recorded_at: datetime
    actor: str
    summary: str
    confidence: float | None = None
    dedupe_key: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ExtractedValue(BaseModel):
    """A single identifier/number extracted by the LLM (FR-15)."""

    key: str
    type: str
    value: str
    normalized: str | None = None
    confidence: float = 1.0


class Note(BaseModel):
    """A free-text note attached to a document or folder, with timestamps."""

    note_id: str
    content: str
    created_at: datetime
    updated_at: datetime


class FolderRef(BaseModel):
    """A document's membership in a folder (n:m, stored only in the database)."""

    folder_id: str
    name: str
    emoji: str | None = None
    is_primary: bool = False


class DocType(BaseModel):
    """A first-class document type (e.g. invoice, contract, meeting_notes).

    A doc-type describes *what a document is*; this is deliberately distinct from
    folders, which describe *where a document is organised*.
    """

    doc_type_id: str
    name: str
    description: str | None = None
    emoji: str | None = None
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class Folder(BaseModel):
    """A first-class, hierarchical folder (replaces the old derived categories)."""

    folder_id: str
    name: str
    description: str | None = None
    emoji: str | None = None
    parent_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    notes: list[Note] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FolderNode(BaseModel):
    """A node in the folder tree (FR-22): a folder plus its children and count."""

    folder_id: str
    name: str
    description: str | None = None
    emoji: str | None = None
    parent_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    document_count: int = 0
    children: list["FolderNode"] = Field(default_factory=list)


class Document(BaseModel):
    """A stored document record (system of record: Postgres)."""

    document_id: str
    title: str
    filename: str = ""
    mime_type: str
    size_bytes: int
    content_hash: str
    minio_object: str
    status: DocumentStatus = DocumentStatus.PENDING
    error: str | None = None

    content_markdown: str | None = None
    doc_type: str | None = None
    doc_type_id: str | None = None
    summary: str | None = None
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    folders: list[FolderRef] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _default_filename_to_title(self) -> "Document":
        """Ensure filename is never empty; fall back to title for legacy/test data."""
        if not self.filename:
            self.filename = self.title
        return self

    @property
    def folder_ids(self) -> list[str]:
        """Direct folder ids this document is a member of."""
        return [ref.folder_id for ref in self.folders]

    @property
    def primary_folder_id(self) -> str | None:
        """The canonical folder id (drives the backup layout), if any."""
        for ref in self.folders:
            if ref.is_primary:
                return ref.folder_id
        return self.folders[0].folder_id if self.folders else None


class Chunk(BaseModel):
    """A chunk/snippet with its embedding (vector index, FR-25).

    Carries denormalised document metadata so the chunk/vector index can be filtered
    on the same fields as the document projection (semantic search filtering, FR-20).
    """

    chunk_id: str
    document_id: str
    ordinal: int
    snippet: str
    embedding: list[float]
    title: str = ""
    doc_type: str | None = None
    folder_ids: list[str] = Field(default_factory=list)
    folder_ancestor_ids: list[str] = Field(default_factory=list)
    value_terms: list[str] = Field(default_factory=list)
    status: DocumentStatus | None = None
    created_at: datetime | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


class SearchHit(BaseModel):
    """A single semantic (chunk-level) search result (FR-21)."""

    document_id: str
    chunk_id: str
    snippet: str
    score: float
    title: str
    filename: str | None = None
    doc_type: str | None = None
    folder_ids: list[str] = Field(default_factory=list)


class DocumentHit(BaseModel):
    """A single keyword (document-level) search result (FR-19/20)."""

    document_id: str
    title: str
    filename: str | None = None
    score: float
    doc_type: str | None = None
    folder_ids: list[str] = Field(default_factory=list)
    snippet: str | None = None


class SearchResultItem(BaseModel):
    """A single fused (RRF) hybrid-search result at the document level (FR-19)."""

    document_id: str
    title: str
    filename: str | None = None
    score: float
    doc_type: str | None = None
    summary: str | None = None
    folder_ids: list[str] = Field(default_factory=list)
    snippet: str | None = None


class HybridSearchResult(BaseModel):
    """Fused hybrid-search result: a single ranked document list (FR-19).

    Keyword (BM25 over the document projection) and semantic (kNN over chunks)
    rankings are merged with Reciprocal Rank Fusion into one document-level ranking.
    """

    results: list[SearchResultItem] = Field(default_factory=list)


class SimilarDocument(BaseModel):
    """A candidate similar document found during ingestion (placement input)."""

    document_id: str
    title: str
    filename: str | None = None
    score: float
    doc_type: str | None = None
    summary: str | None = None
    folder_ids: list[str] = Field(default_factory=list)
    primary_folder_id: str | None = None
    value_terms: list[str] = Field(default_factory=list)


class FolderVote(BaseModel):
    """An aggregated folder score derived from similar documents (placement input)."""

    folder_id: str
    score: float
