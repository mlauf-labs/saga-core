"""Unit tests for the document analyzer (structured-output library integration)."""

from __future__ import annotations

from typing import Any, cast

import pytest
from llm_structured_output import StructuredOutputStats
from pydantic import BaseModel

from docstore.llm import analyzer as analyzer_module
from docstore.llm.analyzer import DocumentAnalyzer
from docstore.llm.prompts import PromptLibrary
from docstore.llm.schemas import Categorization, Classification, ExtractedValueOut, ValueExtraction


class _Recorder:
    """Captures calls and returns scripted results per schema name."""

    def __init__(self, responses: dict[str, BaseModel | None]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        model: Any,
        schema: type[BaseModel],
        text: str,
        *,
        system_prompt: str | None = None,
        fallback_llm_model: Any = None,
        max_primary_retries: int = 3,
        max_fallback_retries: int = 3,
    ) -> tuple[BaseModel | None, StructuredOutputStats]:
        self.calls.append({"schema": schema.__name__, "text": text, "system_prompt": system_prompt})
        return self.responses.get(schema.__name__), StructuredOutputStats()


def _patch(monkeypatch: pytest.MonkeyPatch, responses: dict[str, BaseModel | None]) -> _Recorder:
    recorder = _Recorder(responses)
    monkeypatch.setattr(analyzer_module, "extract_from_text", recorder)
    return recorder


def _analyzer() -> DocumentAnalyzer:
    return DocumentAnalyzer(cast(Any, object()), PromptLibrary("prompts"))


async def test_analyze_combines_all_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(
        monkeypatch,
        {
            "Classification": Classification(doc_type="invoice", confidence=0.9),
            "ValueExtraction": ValueExtraction(
                values=[ExtractedValueOut(key="invoice_number", type="identifier", value="INV-1")]
            ),
            "Categorization": Categorization(paths=["Finance/Invoices", "Finance"]),
        },
    )
    result = await _analyzer().analyze(title="inv.pdf", content="Total due 100 EUR")

    assert result.doc_type == "invoice"
    assert result.extracted_values[0].key == "invoice_number"
    assert result.folder_structure == ["Finance/Invoices", "Finance"]
    # Three extraction calls, each with the document text and a system prompt.
    assert [c["schema"] for c in recorder.calls] == [
        "Classification",
        "ValueExtraction",
        "Categorization",
    ]
    assert all(c["system_prompt"] for c in recorder.calls)
    assert "inv.pdf" in recorder.calls[0]["system_prompt"]


async def test_analyze_classification_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        {
            "Classification": None,
            "ValueExtraction": ValueExtraction(values=[]),
            "Categorization": Categorization(paths=[]),
        },
    )
    result = await _analyzer().analyze(title="x", content="y")
    assert result.doc_type == "unknown"
    assert result.extracted_values == []
    assert result.category_paths == []


async def test_analyze_value_extraction_failure_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        {
            "Classification": Classification(doc_type="invoice"),
            "ValueExtraction": None,
            "Categorization": Categorization(paths=["Finance"]),
        },
    )
    result = await _analyzer().analyze(title="x", content="y")
    assert result.doc_type == "invoice"
    assert result.extracted_values == []
    assert result.category_paths == ["Finance"]


async def test_analyze_truncates_content(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(
        monkeypatch,
        {
            "Classification": Classification(doc_type="other"),
            "ValueExtraction": ValueExtraction(values=[]),
            "Categorization": Categorization(paths=["Misc"]),
        },
    )
    analyzer = DocumentAnalyzer(cast(Any, object()), PromptLibrary("prompts"), max_input_chars=10)
    await analyzer.analyze(title="t", content="X" * 500)
    assert all(len(c["text"]) <= 10 for c in recorder.calls)
