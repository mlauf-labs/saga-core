"""Unit tests for the document analyzer (prompt rendering + parsing + validation)."""

from __future__ import annotations

import pytest

from docstore.core.errors import AnalysisError
from docstore.llm.analyzer import DocumentAnalyzer, _extract_json
from docstore.llm.prompts import PromptLibrary


class ScriptedProvider:
    """LLM provider stub returning queued responses in order."""

    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, *, prompt: str, json_mode: bool = True) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)

    async def aclose(self) -> None:
        return None


def test_extract_json_handles_fences() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_handles_surrounding_text() -> None:
    assert _extract_json('Sure! {"a": 1} done') == '{"a": 1}'


def test_extract_json_no_object_raises() -> None:
    with pytest.raises(AnalysisError):
        _extract_json("no json here")


async def test_analyze_combines_steps() -> None:
    provider = ScriptedProvider(
        [
            '{"doc_type": "invoice", "confidence": 0.9, "rationale": "has total"}',
            '{"values": [{"key": "invoice_number", "type": "identifier", '
            '"value": "INV-1", "normalized": "INV-1", "confidence": 0.8}]}',
            '{"paths": ["Finance/Invoices", "Finance"], "confidence": 0.7}',
        ]
    )
    analyzer = DocumentAnalyzer(provider, PromptLibrary("prompts"))
    result = await analyzer.analyze(title="inv.pdf", content="Total due 100 EUR")

    assert result.doc_type == "invoice"
    assert result.extracted_values[0].key == "invoice_number"
    assert result.folder_structure == ["Finance/Invoices", "Finance"]
    assert result.category_paths[0] == "Finance/Invoices"
    # Three prompts rendered (classification, extraction, categorization).
    assert len(provider.prompts) == 3
    assert "inv.pdf" in provider.prompts[0]


async def test_analyze_coerces_loose_value_shapes() -> None:
    # Small models often return numbers, nested objects, or `type` instead of `key`.
    provider = ScriptedProvider(
        [
            '{"doc_type": "invoice", "confidence": 0.9}',
            '{"values": ['
            '{"type": "invoice_number", "value": "INV-1"},'
            '{"type": "net_amount", "value": 184.5},'
            '{"type": "bill_to", "value": {"name": "Jane", "city": "Springfield"}}'
            ']}',
            '{"paths": ["Finance/Invoices"], "confidence": 0.7}',
        ]
    )
    analyzer = DocumentAnalyzer(provider, PromptLibrary("prompts"))
    result = await analyzer.analyze(title="inv.pdf", content="total")
    by_key = {v.key: v.value for v in result.extracted_values}
    assert by_key["invoice_number"] == "INV-1"
    assert by_key["net_amount"] == "184.5"
    assert "Jane" in by_key["bill_to"]


async def test_analyze_step_failure_is_non_fatal() -> None:
    # Classification ok, value-extraction malformed, categorization ok.
    provider = ScriptedProvider(
        [
            '{"doc_type": "invoice", "confidence": 0.9}',
            "totally not json",
            '{"paths": ["Finance"], "confidence": 0.5}',
        ]
    )
    analyzer = DocumentAnalyzer(provider, PromptLibrary("prompts"))
    result = await analyzer.analyze(title="x", content="y")
    assert result.doc_type == "invoice"
    assert result.extracted_values == []
    assert result.category_paths == ["Finance"]


async def test_analyze_classification_failure_falls_back() -> None:
    provider = ScriptedProvider(["nope", '{"values": []}', '{"paths": []}'])
    analyzer = DocumentAnalyzer(provider, PromptLibrary("prompts"))
    result = await analyzer.analyze(title="x", content="y")
    assert result.doc_type == "unknown"


async def test_truncation_limits_content() -> None:
    provider = ScriptedProvider(
        [
            '{"doc_type": "other", "confidence": 1.0}',
            '{"values": []}',
            '{"paths": ["Misc"], "confidence": 1.0}',
        ]
    )
    analyzer = DocumentAnalyzer(provider, PromptLibrary("prompts"), max_input_chars=10)
    await analyzer.analyze(title="t", content="X" * 500)
    # The very long content must have been truncated in the rendered prompt.
    assert provider.prompts[0].count("X") <= 10
