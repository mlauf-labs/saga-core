"""Validated schemas for LLM analysis outputs (FR-14/15/16, FR-18).

Parsing is deliberately lenient (FR-18: flag/normalise rather than fail): smaller
LLMs often return numbers instead of strings, nest objects, or use ``type`` in place
of ``key``. We coerce these into the canonical shape instead of rejecting the document.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from docstore.core.models import ExtractedValue


def _stringify(value: Any) -> str:  # noqa: ANN401 - intentionally accepts any LLM output
    """Coerce an arbitrary LLM-provided value into a string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


class Classification(BaseModel):
    """Result of document-type classification (FR-14)."""

    doc_type: str
    confidence: float = 1.0
    rationale: str = ""


class ExtractedValueOut(BaseModel):
    """A single extracted identifier/number as returned by the LLM (FR-15)."""

    key: str = ""
    type: str = "other"
    value: str = ""
    normalized: str | None = None
    confidence: float = 1.0

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:  # noqa: ANN401 - tolerant of varied LLM shapes
        if not isinstance(data, dict):
            return data
        coerced = dict(data)
        if not coerced.get("key") and coerced.get("type"):
            coerced["key"] = coerced["type"]
        if "value" in coerced and coerced["value"] is not None:
            coerced["value"] = _stringify(coerced["value"])
        normalized = coerced.get("normalized")
        if normalized is not None:
            coerced["normalized"] = _stringify(normalized)
        return coerced

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
