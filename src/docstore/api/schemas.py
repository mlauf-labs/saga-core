"""Request/response schemas for the REST API (NFR-2, NFR-20)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from docstore.core.models import Document, DocumentStatus, ExtractedValue


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
