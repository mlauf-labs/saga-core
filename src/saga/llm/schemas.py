"""Validated schemas for LLM analysis outputs (FR-14/15/16, FR-18).

Parsing is deliberately lenient (FR-18: flag/normalise rather than fail): smaller
LLMs often return numbers instead of strings, nest objects, or use ``type`` in place
of ``key``. We coerce these into the canonical shape instead of rejecting the document.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from saga.core.models import ExtractedValue
from saga.llm.rrule import validate_rrule

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


class EmojiSuggestion(BaseModel):
    """A single emoji suggested for a doc-type or folder (UI helper)."""

    model_config = _LLM_MODEL_CONFIG

    emoji: str = Field(
        description=(
            "A single emoji that visually represents the given doc-type or folder, "
            "e.g. '📄' for invoice, '💰' for Finance, '🏥' for Health. Must be "
            "exactly one emoji character."
        )
    )
    rationale: str = Field(
        default="",
        description="One short sentence justifying the chosen emoji.",
    )


class DocTypeAssignment(BaseModel):
    """The single document type chosen for a document (FR-14).

    A doc-type describes *what a document is* (invoice, contract, meeting_notes, ...).
    The model either reuses an existing doc-type or coins a new one with a short
    description of when that type applies. This is distinct from folders (organisation).
    """

    model_config = _LLM_MODEL_CONFIG

    doc_type: str = Field(
        description=(
            "The single canonical document type, in lowercase snake_case. Reuse an "
            "existing type from the provided catalog when one fits; otherwise coin a "
            "concise new snake_case label, e.g. 'invoice', 'meeting_notes', 'contract'."
        )
    )
    is_new: bool = Field(
        default=False,
        description="True if this is a brand-new type not present in the provided catalog.",
    )
    description: str | None = Field(
        default=None,
        description=(
            "Only when is_new is true: one short sentence describing when a document "
            "should receive this type. Null when reusing an existing type."
        ),
    )
    emoji: str | None = Field(
        default=None,
        description=(
            "Only when is_new is true: a single emoji that visually represents this "
            "document type, e.g. '📄' for invoice, '📅' for meeting_notes, '📝' for "
            "contract. Must be exactly one emoji character. Null when reusing an "
            "existing type."
        ),
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

    @model_validator(mode="before")
    @classmethod
    def _coerce_values_list(cls, data: Any) -> Any:  # noqa: ANN401
        """Unwrap ``values`` when a model serialises the whole array as a JSON string.

        Some smaller models (e.g. llama3.1:8b) embed the items array as a
        JSON-encoded string inside the tool-call arguments instead of a real
        array.  We silently decode it so no validation retry is needed.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("values")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return {**data, "values": parsed}
            except (ValueError, json.JSONDecodeError):
                pass
        return data


class TimelineEventOut(BaseModel):
    """A single dated event, appointment, or recurring obligation found in the text."""

    model_config = _LLM_MODEL_CONFIG

    kind: str = Field(
        default="past",
        description=(
            "One of: 'past' (something that happened on a date), 'future' (an upcoming "
            "appointment/deadline), or 'recurring' (a repeating obligation)."
        ),
    )
    description: str = Field(
        default="",
        description="A short human-readable description of the event, e.g. 'Policy concluded'.",
    )
    date: str = Field(
        default="",
        description=(
            "The event's date as ISO 'YYYY-MM-DD'. For a recurring event, the start/anchor "
            "date. Resolve relative expressions only against a reference date stated in the "
            "document; otherwise leave empty."
        ),
    )
    end_date: str | None = Field(
        default=None,
        description=(
            "Optional ISO 'YYYY-MM-DD' end date for a period or a recurrence that ends. "
            "Null when it is a single point in time or the end is open/unknown."
        ),
    )
    recurrence: str | None = Field(
        default=None,
        description=(
            "For 'recurring' events only: an RRULE pattern (RFC 5545), e.g. 'FREQ=YEARLY'. "
            "Describe only the repetition pattern; do not encode the end (use end_date). "
            "Null for non-recurring events."
        ),
    )
    source_quote: str = Field(
        default="",
        description="A short verbatim quote from the document supporting this event.",
    )
    confidence: float = Field(
        default=1.0,
        description="How confident/relevant this event is, from 0.0 to 1.0.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:  # noqa: ANN401 - tolerant of varied LLM shapes
        if not isinstance(data, dict):
            return data
        coerced = dict(data)
        for key in ("kind", "description", "date", "source_quote"):
            if coerced.get(key) is not None:
                coerced[key] = _stringify(coerced[key])
        for key in ("end_date", "recurrence"):
            value = coerced.get(key)
            if value is not None and value != "":
                coerced[key] = _stringify(value)
            elif value == "":
                coerced[key] = None
        return coerced

    @field_validator("recurrence")
    @classmethod
    def _validate_recurrence(cls, value: str | None) -> str | None:
        """Reject invalid RRULEs so saidex makes the LLM self-correct (Phase 3)."""
        if value is None or value == "":
            return value
        return validate_rrule(value)


class TimelineExtraction(BaseModel):
    """All dated events extracted from a document (Phase 2 content stream)."""

    model_config = _LLM_MODEL_CONFIG

    events: list[TimelineEventOut] = Field(
        default_factory=list,
        description=(
            "Every dated event, appointment, deadline, or recurring obligation described in "
            "the document. Return an empty list if none are present. Do NOT restate raw "
            "identifiers or numbers (those are captured separately)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_events_list(cls, data: Any) -> Any:  # noqa: ANN401
        """Unwrap ``events`` when a model serialises the array as a JSON string."""
        if not isinstance(data, dict):
            return data
        raw = data.get("events")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return {**data, "events": parsed}
            except (ValueError, json.JSONDecodeError):
                pass
        return data


class Summary(BaseModel):
    """A short title and summary for a document (FR-14)."""

    model_config = _LLM_MODEL_CONFIG

    title: str = Field(
        default="",
        description=(
            "A concise, human-readable title for the document (a few words). "
            "Use the filename as a hint, but write a meaningful title that describes the "
            "document's actual content — do NOT simply copy the filename verbatim. "
            "Keep it short (≤ 10 words). Must be in the configured output language."
        ),
    )
    summary: str = Field(
        description=(
            "A concise 1-3 sentence summary describing what the document is about, "
            "without going into fine detail. Plain prose, no bullet points."
        )
    )


class NewFolder(BaseModel):
    """A proposal to create a new folder during placement (FR-16).

    Supports creating arbitrarily deep hierarchies in a single LLM turn:
    - To nest under an **existing** folder: set ``parent_id`` to that folder's id.
    - To nest under **another new** folder in the same batch: set ``parent_name`` to
      that folder's ``name`` value.  The pipeline processes proposals in topological
      order so a child is always created after its parent.
    - Root folders: leave both ``parent_id`` and ``parent_name`` null.
    """

    model_config = _LLM_MODEL_CONFIG

    name: str = Field(description="Short, human-readable folder name, e.g. 'Invoices'.")
    parent_id: str | None = Field(
        default=None,
        description=(
            "The id of an **existing** folder (from the provided folder tree) to nest "
            "this new folder under. Null if the parent is a new folder (use parent_name) "
            "or if this should be a root folder."
        ),
    )
    parent_name: str | None = Field(
        default=None,
        description=(
            "The 'name' of another NEW folder in this same new_folders list to nest "
            "this folder under. Use this when you also need to create the parent in "
            "this batch. Ignored if parent_id is set."
        ),
    )
    description: str | None = Field(
        default=None,
        description="One short sentence describing what belongs in this folder.",
    )
    emoji: str | None = Field(
        default=None,
        description=(
            "A single emoji that visually represents this folder's content, e.g. '💰' "
            "for Finance, '📋' for Invoices, '🏥' for Health. Must be exactly one emoji "
            "character. Used only for display; not part of the folder name."
        ),
    )


class FolderPlacement(BaseModel):
    """The folder placement decision for a document (FR-16/17).

    A document may be placed into multiple existing folders and/or newly created ones.
    ``primary`` marks the canonical folder (drives the backup layout). When the model
    creates new folders, it references them by name in ``new_folder_primary`` instead.
    """

    model_config = _LLM_MODEL_CONFIG

    assignments: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of EXISTING folders (from the provided tree) the document should be "
            "placed into. May be empty if only new folders are proposed."
        ),
    )
    new_folders: list[NewFolder] = Field(
        default_factory=list,
        description=(
            "New folders to create. All proposed new folders are created AND the document "
            "is placed in every one of them (in addition to any existing folders in "
            "'assignments'). You may propose multiple new folders at different levels of "
            "the hierarchy or at the same level. Keep this empty when existing folders "
            "already cover the document well."
        ),
    )
    primary: str | None = Field(
        default=None,
        description=(
            "The id of the existing folder (from 'assignments') that is the single best "
            "(canonical) fit. Null when the best fit is a newly created folder."
        ),
    )
    new_folder_primary: str | None = Field(
        default=None,
        description=(
            "The name of a newly created folder (from 'new_folders') that is the "
            "canonical fit, used only when 'primary' is null."
        ),
    )
    rationale: str = Field(
        default="",
        description="One or two short sentences justifying the placement.",
    )


class FolderDecision(BaseModel):
    """Final answer of the folder-placement agent (agentic path, FR-16/17).

    After the agent has used ``create_folder`` tool calls to build the required
    folder hierarchy, it submits a ``FolderDecision`` as its final answer.
    Only the deepest / most specific folder IDs are listed in ``assignments`` —
    structural ancestor folders are created but NOT included here.
    """

    model_config = _LLM_MODEL_CONFIG

    assignments: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of the folders (existing or just created via create_folder) where the "
            "document should be directly filed. Include ONLY the deepest/most specific "
            "folder(s) — do NOT include ancestor folders. Typically a single id."
        ),
    )
    primary: str | None = Field(
        default=None,
        description=(
            "The id of the single best (canonical) folder from 'assignments'. "
            "Must be one of the ids listed in 'assignments'."
        ),
    )
    rationale: str = Field(
        default="",
        description="One or two short sentences justifying the placement decision.",
    )
