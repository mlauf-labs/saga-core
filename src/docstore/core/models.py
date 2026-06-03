"""Typed domain models shared across the application (NFR-2).

This module intentionally does NOT use ``from __future__ import annotations`` so
that Pydantic can resolve runtime types (e.g. ``datetime``) eagerly.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentStatus(StrEnum):
    """Lifecycle status of a document (FR-12)."""

    PENDING = "pending"
    CONVERTING = "converting"
    ANALYZING = "analyzing"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class ExtractedValue(BaseModel):
    """A single identifier/number extracted by the LLM (FR-15)."""

    key: str
    type: str
    value: str
    normalized: str | None = None
    confidence: float = 1.0


class Document(BaseModel):
    """A stored document record (document index, FR-25)."""

    document_id: str
    title: str
    mime_type: str
    size_bytes: int
    content_hash: str
    minio_object: str
    status: DocumentStatus = DocumentStatus.PENDING
    error: str | None = None

    content_markdown: str | None = None
    doc_type: str | None = None
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    # Ordered hierarchical paths; first entry is canonical (FR-17).
    folder_structure: list[str] = Field(default_factory=list)
    category_paths: list[str] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime


class Chunk(BaseModel):
    """A chunk/snippet with its embedding (vector index, FR-25)."""

    chunk_id: str
    document_id: str
    ordinal: int
    snippet: str
    embedding: list[float]
    doc_type: str | None = None
    category_paths: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    """A single hybrid-search result (FR-21)."""

    document_id: str
    chunk_id: str
    snippet: str
    score: float
    title: str
    doc_type: str | None = None
    category_paths: list[str] = Field(default_factory=list)


class CategoryNode(BaseModel):
    """A node in the derived category tree (FR-22)."""

    path: str
    name: str
    document_count: int
    children: list["CategoryNode"] = Field(default_factory=list)
