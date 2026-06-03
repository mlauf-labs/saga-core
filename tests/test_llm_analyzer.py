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


async def test_analyze_invalid_json_raises() -> None:
    provider = ScriptedProvider(["not json at all"])
    analyzer = DocumentAnalyzer(provider, PromptLibrary("prompts"))
    with pytest.raises(AnalysisError):
        await analyzer.analyze(title="x", content="y")


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
