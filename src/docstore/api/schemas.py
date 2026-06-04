"""Request/response schemas for the REST API (NFR-2, NFR-20)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from docstore.core.models import CategoryNode, Document, DocumentStatus, ExtractedValue, SearchHit


class ErrorResponse(BaseModel):
    """Standard error envelope with an actionable message (NFR-15)."""

    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable, actionable error description.")


class UploadAcceptedResponse(BaseModel):
    """Returned when a document is accepted for asynchronous processing (FR-1/FR-12)."""

    document_id: str
    status: DocumentStatus
    title: str


class DocumentStatusResponse(BaseModel):
    """Lightweight processing-status view (FR-12)."""

    document_id: str
    status: DocumentStatus
    error: str | None = None


class DocumentResponse(BaseModel):
    """Full document view returned by the API."""

    document_id: str
    title: str
    mime_type: str
    size_bytes: int
    content_hash: str
    status: DocumentStatus
    error: str | None = None
    doc_type: str | None = None
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    folder_structure: list[str] = Field(default_factory=list)
    category_paths: list[str] = Field(default_factory=list)
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


class SearchRequest(BaseModel):
    """Hybrid search request (FR-19/20)."""

    query: str = Field(min_length=1, description="Natural-language query or keywords.")
    top_k: int | None = Field(default=None, ge=1, description="Number of results.")
    doc_type: str | None = Field(default=None, description="Restrict to a document type.")
    category_path: str | None = Field(
        default=None, description="Restrict to a category subtree, e.g. 'Insurance/Health'."
    )
    title: str | None = Field(default=None, description="Restrict to an exact document title.")
    filters: dict[str, str] = Field(
        default_factory=dict, description="Match extracted values, e.g. {invoice_number: '12'}."
    )


class DocumentSearchRequest(BaseModel):
    """Keyword document search over title/content/metadata with filters (FR-20)."""

    query: str | None = Field(
        default=None, description="Keyword query over title/content/metadata; empty = browse."
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=0, ge=0, description="0 = configured default page size.")
    doc_type: str | None = Field(default=None, description="Filter by document type.")
    category_path: str | None = Field(
        default=None, description="Filter by a category subtree, e.g. 'Insurance/Health'."
    )
    title: str | None = Field(default=None, description="Filter by exact document title.")
    status: str | None = Field(default=None, description="Filter by processing status.")
    filters: dict[str, str] = Field(
        default_factory=dict, description="Match extracted values, e.g. {invoice_number: '12'}."
    )


class DocumentMetadataPatch(BaseModel):
    """Editable document metadata (PATCH: provided fields replace, omitted unchanged)."""

    doc_type: str | None = None
    extracted_values: list[ExtractedValue] | None = None
    folder_structure: list[str] | None = None
    category_paths: list[str] | None = None

    def is_empty(self) -> bool:
        return self.model_dump(exclude_unset=True) == {}


class SearchResponse(BaseModel):
    """Ranked hybrid-search results (FR-21)."""

    query: str
    hits: list[SearchHit]


class CategoryTreeResponse(BaseModel):
    """The derived category tree (FR-22)."""

    tree: list[CategoryNode]


class ExportPageResponse(BaseModel):
    """A page of the full-export stream (FR-28).

    ``items`` include the converted Markdown and all metadata. ``next_cursor`` is an
    opaque token to fetch the next page; ``null`` on the last page.
    """

    items: list[DocumentResponse]
    next_cursor: str | None = None
