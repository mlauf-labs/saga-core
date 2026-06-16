"""Request/response schemas for the REST API (NFR-2, NFR-20)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from saga.core.models import (
    Document,
    DocumentStatus,
    Event,
    ExtractedValue,
    FolderRef,
    Note,
    SearchResultItem,
)


class ErrorResponse(BaseModel):
    """Standard error envelope with an actionable message (NFR-15)."""

    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable, actionable error description.")


class UploadAcceptedResponse(BaseModel):
    """Returned when a document is accepted for asynchronous processing (FR-1/FR-12)."""

    document_id: str
    status: DocumentStatus
    title: str
    filename: str


class DocumentStatusResponse(BaseModel):
    """Lightweight processing-status view (FR-12)."""

    document_id: str
    status: DocumentStatus
    error: str | None = None


class DocumentResponse(BaseModel):
    """Full document view returned by the API."""

    document_id: str
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    content_hash: str
    status: DocumentStatus
    error: str | None = None
    doc_type: str | None = None
    doc_type_id: str | None = None
    summary: str | None = None
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    folders: list[FolderRef] = Field(default_factory=list)
    primary_folder_path: list[str] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    content_markdown: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: Document, *, include_content: bool) -> DocumentResponse:
        data = document.model_dump()
        if not include_content:
            data["content_markdown"] = None
        return cls.model_validate(data)


class DocumentListResponse(BaseModel):
    """Paginated list of documents (FR-28, NFR-13)."""

    items: list[DocumentResponse]
    page: int
    page_size: int
    total: int


class DocumentPatch(BaseModel):
    """Editable document fields (PATCH: provided fields replace, omitted unchanged)."""

    title: str | None = None
    summary: str | None = None
    doc_type_id: str | None = None
    extracted_values: list[ExtractedValue] | None = None

    def is_empty(self) -> bool:
        return self.model_dump(exclude_unset=True) == {}


class SearchRequest(BaseModel):
    """Fused hybrid search request (FR-19/20/21).

    At least one of ``keyword_query`` or ``semantic_query`` must be provided.
    """

    keyword_query: str | None = Field(
        default=None,
        description=(
            "OpenSearch query_string over the document projection (AND/OR/NOT, "
            "field:value, quoted phrases, * / ? wildcards)."
        ),
    )
    semantic_query: str | None = Field(
        default=None,
        description="Natural-language question for semantic (vector) search over chunks.",
    )
    top_k: int | None = Field(default=None, ge=1, description="Max fused results.")
    doc_type: str | None = Field(default=None, description="Restrict to a document type.")
    folder_id: str | None = Field(
        default=None, description="Restrict to a folder (and, by default, its subtree)."
    )
    include_subtree: bool = Field(
        default=True, description="Include documents in descendant folders."
    )
    title: str | None = Field(default=None, description="Restrict to an exact document title.")
    status: str | None = Field(default=None, description="Restrict to a processing status.")
    created_from: str | None = Field(
        default=None, description="Only documents created on/after this ISO date/datetime."
    )
    created_to: str | None = Field(
        default=None, description="Only documents created on/before this ISO date/datetime."
    )
    filters: dict[str, str] = Field(
        default_factory=dict, description="Match extracted values, e.g. {invoice_number: '12'}."
    )


class DocumentSearchRequest(BaseModel):
    """Keyword document search over title/summary/content/metadata with filters (FR-20)."""

    query: str | None = Field(
        default=None, description="Keyword query; empty = browse with filters only."
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=0, ge=0, description="0 = configured default page size.")
    doc_type: str | None = Field(default=None, description="Filter by document type.")
    folder_id: str | None = Field(default=None, description="Filter by a folder (subtree).")
    include_subtree: bool = Field(default=True, description="Include descendant folders.")
    title: str | None = Field(default=None, description="Filter by exact document title.")
    status: str | None = Field(default=None, description="Filter by processing status.")
    filters: dict[str, str] = Field(
        default_factory=dict, description="Match extracted values, e.g. {invoice_number: '12'}."
    )


class SearchResponse(BaseModel):
    """Fused hybrid-search results: a single ranked document list (FR-19)."""

    results: list[SearchResultItem] = Field(default_factory=list)


class FolderCreate(BaseModel):
    """Create a folder (FR-16)."""

    name: str = Field(description="Folder name (unique under its parent).")
    description: str | None = Field(default=None, description="Context for the LLM/users.")
    parent_id: str | None = Field(default=None, description="Parent folder id, or null for root.")
    metadata: dict[str, str] = Field(default_factory=dict)
    emoji: str | None = Field(default=None, description="Single emoji for visual illustration.")


class FolderUpdate(BaseModel):
    """Update a folder (rename/move/describe/metadata). Omitted fields are unchanged."""

    name: str | None = None
    description: str | None = None
    parent_id: str | None = None
    metadata: dict[str, str] | None = None
    emoji: str | None = None


class NoteCreate(BaseModel):
    content: str = Field(description="Free-text note content.")


class NoteUpdate(BaseModel):
    content: str = Field(description="Replacement note content.")


class DocTypeCreate(BaseModel):
    name: str = Field(description="Doc-type name, e.g. 'invoice'.")
    description: str | None = Field(default=None, description="When to use this type.")
    emoji: str | None = Field(default=None, description="Single emoji for visual illustration.")


class DocTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    emoji: str | None = None


class EmojiSuggestRequest(BaseModel):
    """Ask the LLM for an emoji matching a doc-type or folder (UI helper)."""

    kind: Literal["doc_type", "folder"] = Field(description="What the emoji is for.")
    name: str = Field(description="Name of the doc-type or folder.")
    description: str | None = Field(default=None, description="Optional description.")


class EmojiSuggestResponse(BaseModel):
    emoji: str = Field(description="The suggested emoji (single character).")


class MembershipSetRequest(BaseModel):
    """Replace a document's full folder membership set (FR-16)."""

    folder_ids: list[str] = Field(default_factory=list)
    primary_id: str | None = Field(default=None, description="Which folder is canonical.")


class MembershipResponse(BaseModel):
    folders: list[FolderRef] = Field(default_factory=list)


class ExportPageResponse(BaseModel):
    """A page of the full-export stream (FR-28)."""

    items: list[DocumentResponse]
    next_cursor: str | None = None


class TimelineResponse(BaseModel):
    """A page of timeline events (audit and/or content)."""

    items: list[Event]
    limit: int
    offset: int
