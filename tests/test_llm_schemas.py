"""Tests that LLM-filled schemas expose field descriptions to the model.

The structured-output library converts each schema with ``convert_to_openai_tool``;
the model docstring becomes the tool description and every ``Field(description=...)``
is passed to the LLM as the primary signal for what to put in each field.
"""

from __future__ import annotations

from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool

from docstore.llm.schemas import (
    Categorization,
    Classification,
    ExtractedValueOut,
    ValueExtraction,
)


def _properties(schema: type) -> dict[str, Any]:
    tool = convert_to_openai_tool(schema)
    properties: dict[str, Any] = tool["function"]["parameters"]["properties"]
    return properties


def test_classification_fields_have_descriptions() -> None:
    props = _properties(Classification)
    assert "snake_case" in props["doc_type"]["description"]
    assert props["confidence"]["description"]
    assert props["rationale"]["description"]


def test_extracted_value_fields_have_descriptions() -> None:
    props = _properties(ExtractedValueOut)
    for field in ("key", "type", "value", "normalized", "confidence"):
        assert props[field].get("description"), f"{field} is missing a description"
    assert "verbatim" in props["value"]["description"].lower()
    assert "iso-8601" in props["normalized"]["description"].lower()


def test_value_extraction_list_described() -> None:
    props = _properties(ValueExtraction)
    assert "empty list" in props["values"]["description"].lower()


def test_categorization_describes_canonical_first_path() -> None:
    props = _properties(Categorization)
    description = props["paths"]["description"].lower()
    assert "first" in description
    assert "/" in props["paths"]["description"]


def test_tool_description_comes_from_docstring() -> None:
    tool = convert_to_openai_tool(Classification)
    assert "document type" in tool["function"]["description"].lower()
