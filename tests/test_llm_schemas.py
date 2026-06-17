"""Unit tests for the current LLM-output Pydantic schemas (FR-14/15/16, FR-18).

These cover required fields, defaults, lenient parsing/coercion (FR-18) and the
conversion of an extracted value into its domain model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from saga.core.models import ExtractedValue
from saga.llm.schemas import (
    DocTypeAssignment,
    ExtractedValueOut,
    FolderPlacement,
    NewFolder,
    Summary,
    TimelineEventOut,
    TimelineExtraction,
    ValueExtraction,
)


def test_doc_type_assignment_defaults() -> None:
    assignment = DocTypeAssignment(doc_type="invoice")
    assert assignment.doc_type == "invoice"
    assert assignment.is_new is False
    assert assignment.description is None
    assert assignment.rationale == ""


def test_doc_type_assignment_requires_doc_type() -> None:
    with pytest.raises(ValidationError):
        DocTypeAssignment()  # type: ignore[call-arg]


def test_doc_type_assignment_strips_whitespace_and_ignores_extra() -> None:
    assignment = DocTypeAssignment.model_validate(
        {"doc_type": "  contract  ", "is_new": True, "unexpected": "drop me"}
    )
    assert assignment.doc_type == "contract"
    assert assignment.is_new is True


def test_summary_requires_text() -> None:
    assert Summary(summary="A short note.").summary == "A short note."
    with pytest.raises(ValidationError):
        Summary()  # type: ignore[call-arg]


def test_new_folder_defaults() -> None:
    folder = NewFolder(name="Health")
    assert folder.name == "Health"
    assert folder.parent_id is None
    assert folder.description is None


def test_folder_placement_defaults_are_empty() -> None:
    placement = FolderPlacement()
    assert placement.assignments == []
    assert placement.new_folders == []
    assert placement.primary is None
    assert placement.new_folder_primary is None
    assert placement.rationale == ""


def test_folder_placement_parses_nested_new_folders() -> None:
    placement = FolderPlacement.model_validate(
        {
            "assignments": ["f1", "f2"],
            "new_folders": [{"name": "Taxes", "parent_id": "f1"}],
            "primary": "f1",
            "rationale": "Fits finance.",
        }
    )
    assert placement.assignments == ["f1", "f2"]
    assert isinstance(placement.new_folders[0], NewFolder)
    assert placement.new_folders[0].name == "Taxes"
    assert placement.new_folders[0].parent_id == "f1"
    assert placement.primary == "f1"


def test_value_extraction_default_empty_list() -> None:
    assert ValueExtraction().values == []


def test_value_extraction_unwraps_values_serialised_as_json_string() -> None:
    """Smaller models often return `values` as a JSON-encoded string instead of an array.

    The model_validator must silently decode it so no validation retry is wasted
    (regression guard for the llama3.1:8b failure mode observed in production).
    """
    raw = (
        '[{"key": "invoice_number", "type": "identifier", "value": "INV-001",'
        ' "normalized": null, "confidence": 0.99}]'
    )
    ve = ValueExtraction.model_validate({"values": raw})
    assert len(ve.values) == 1
    assert ve.values[0].key == "invoice_number"
    assert ve.values[0].value == "INV-001"


def test_value_extraction_leaves_malformed_string_for_normal_validation() -> None:
    """A non-JSON string in `values` should be left untouched (Pydantic will reject it)."""
    with pytest.raises(ValueError):
        ValueExtraction.model_validate({"values": "not-json-at-all"})


def test_extracted_value_defaults() -> None:
    value = ExtractedValueOut()
    assert value.key == ""
    assert value.type == "other"
    assert value.value == ""
    assert value.normalized is None
    assert value.confidence == 1.0


def test_extracted_value_coerces_key_from_type() -> None:
    value = ExtractedValueOut.model_validate({"type": "iban", "value": "DE00"})
    assert value.key == "iban"


def test_extracted_value_stringifies_non_string_value_and_normalized() -> None:
    value = ExtractedValueOut.model_validate(
        {"key": "amount", "value": 184.5, "normalized": {"raw": 184.5}}
    )
    assert value.value == "184.5"
    assert value.normalized == '{"raw":184.5}'


def test_extracted_value_to_model() -> None:
    out = ExtractedValueOut(key="invoice_number", type="identifier", value="INV-1", confidence=0.8)
    model = out.to_model()
    assert isinstance(model, ExtractedValue)
    assert model.key == "invoice_number"
    assert model.type == "identifier"
    assert model.value == "INV-1"
    assert model.confidence == 0.8


def test_timeline_extraction_parses_and_defaults() -> None:
    raw = {
        "events": [
            {
                "kind": "future",
                "description": "Policy expiry",
                "date": "2027-04-30",
                "confidence": 0.9,
            }
        ]
    }
    ex = TimelineExtraction.model_validate(raw)
    assert ex.events[0].kind == "future"
    assert ex.events[0].end_date is None
    assert ex.events[0].recurrence is None
    assert ex.events[0].confidence == 0.9


def test_timeline_event_defaults_kind_past() -> None:
    ev = TimelineEventOut.model_validate({"description": "x", "date": "2026-01-01"})
    assert ev.kind == "past"
    assert ev.confidence == 1.0


def test_timeline_extraction_unwraps_json_string_events() -> None:
    ex = TimelineExtraction.model_validate({"events": '[{"description":"x","date":"2026-01-01"}]'})
    assert ex.events[0].date == "2026-01-01"
