"""Validated schemas for LLM analysis outputs (FR-14/15/16, FR-18)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from docstore.core.models import ExtractedValue


class Classification(BaseModel):
    """Result of document-type classification (FR-14)."""

    doc_type: str
    confidence: float = 1.0
    rationale: str = ""


class ExtractedValueOut(BaseModel):
    """A single extracted identifier/number as returned by the LLM (FR-15)."""

    key: str
    type: str = "other"
    value: str
    normalized: str | None = None
    confidence: float = 1.0

    def to_model(self) -> ExtractedValue:
        return ExtractedValue(
            key=self.key,
            type=self.type,
            value=self.value,
            normalized=self.normalized,
            confidence=self.confidence,
        )


class ValueExtraction(BaseModel):
    """Wrapper around the list of extracted values (FR-15)."""

    values: list[ExtractedValueOut] = Field(default_factory=list)


class Categorization(BaseModel):
    """Hierarchical category paths for a document (FR-16/17)."""

    paths: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class AnalysisResult(BaseModel):
    """Combined analysis output persisted as document metadata."""

    doc_type: str
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    # Ordered hierarchical paths; first entry is canonical (FR-17).
    folder_structure: list[str] = Field(default_factory=list)
    category_paths: list[str] = Field(default_factory=list)
