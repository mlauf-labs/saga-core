"""Validated schemas for LLM analysis outputs (FR-14/15/16, FR-18).

Parsing is deliberately lenient (FR-18: flag/normalise rather than fail): smaller
LLMs often return numbers instead of strings, nest objects, or use ``type`` in place
of ``key``. We coerce these into the canonical shape instead of rejecting the document.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docstore.core.models import ExtractedValue

# Applied to every LLM-filled schema: trim stray whitespace the model adds and
# silently drop any extra keys it returns, avoiding needless validation retries.
_LLM_MODEL_CONFIG = ConfigDict(str_strip_whitespace=True, extra="ignore")


def _stringify(value: Any) -> str:  # noqa: ANN401 - intentionally accepts any LLM output
    """Coerce an arbitrary LLM-provided value into a string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


class Classification(BaseModel):
    """The single document type that best describes the document's content."""

    model_config = _LLM_MODEL_CONFIG

    doc_type: str = Field(
        description=(
            "The single canonical document type, in lowercase snake_case. Prefer a "
            "known label such as invoice, contract, insurance_policy, letter, receipt, "
            "id_document, bank_statement, payslip, tax_document, certificate, report, "
            "email, form, or other. If none fits, coin a concise snake_case label. "
            "Examples: 'invoice', 'insurance_policy'."
        )
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence in the classification, from 0.0 (unsure) to 1.0 (certain).",
    )
    rationale: str = Field(
        default="",
        description="One short sentence justifying the chosen document type.",
    )


class ExtractedValueOut(BaseModel):
    """A single identifier or numeric value found verbatim in the document."""

    model_config = _LLM_MODEL_CONFIG

    key: str = Field(
        default="",
        description=(
            "A short snake_case name for the value, e.g. 'invoice_number', "
            "'contract_number', 'customer_number', 'order_number', 'phone_number', "
            "'email', 'iban', 'vat_id', 'amount', 'date'. Lowercase, no spaces."
        ),
    )
    type: str = Field(
        default="other",
        description=(
            "The kind of value. One of: 'identifier' (invoice/contract/customer/order "
            "numbers etc.), 'phone', 'email', 'iban', 'amount' (monetary value), "
            "'date', 'percentage', or 'other'."
        ),
    )
    value: str = Field(
        default="",
        description=(
            "The value exactly as it appears in the document, verbatim, as a string "
            "(e.g. 'INV-2026-00417', '184.50 EUR', '12.05.2026'). Do not invent values."
        ),
    )
    normalized: str | None = Field(
        default=None,
        description=(
            "A normalized form of the value, or null if not applicable: dates as "
            "ISO-8601 'YYYY-MM-DD', monetary amounts as a plain decimal without "
            "thousands separators or currency (e.g. '184.50'), phone numbers in E.164 "
            "(e.g. '+4930123456'). Null when no sensible normalization applies."
        ),
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence that this value is correct, from 0.0 to 1.0.",
    )

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
    """All relevant identifiers and numeric values extracted from the document."""

    model_config = _LLM_MODEL_CONFIG

    values: list[ExtractedValueOut] = Field(
        default_factory=list,
        description=(
            "Every relevant identifier and numeric value found in the document "
            "(phone/invoice/contract/customer numbers, IBANs, tax/VAT IDs, dates, "
            "monetary amounts, percentages, ...). Return an empty list if none are present."
        ),
    )


class Categorization(BaseModel):
    """Hierarchical category placement of the document in the archive tree (FR-16/17)."""

    model_config = _LLM_MODEL_CONFIG

    paths: list[str] = Field(
        default_factory=list,
        description=(
            "One or more hierarchical category paths using '/' as the level separator "
            "and Title Case per level (e.g. 'Insurance/Health', 'Finance/Invoices/2026', "
            "'Legal/Contracts'). Prefer 2-4 levels and reuse common top-level categories "
            "(Insurance, Finance, Legal, Personal, Work, Health, Taxes, Property, "
            "Vehicles). The FIRST path is the single best fit (canonical) and drives the "
            "backup folder layout. Return at least one path."
        ),
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence in the categorisation, from 0.0 to 1.0.",
    )


class AnalysisResult(BaseModel):
    """Combined analysis output persisted as document metadata."""

    doc_type: str
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    # Ordered hierarchical paths; first entry is canonical (FR-17).
    folder_structure: list[str] = Field(default_factory=list)
    category_paths: list[str] = Field(default_factory=list)
