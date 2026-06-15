"""Unit tests for the DocumentAnalyzer (structured-output library integration).

``extract_from_text`` is patched with a recorder so no real LLM/network is used; the
recorder returns scripted Pydantic results per schema name and captures the system
prompt / document text passed to each step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from saidex import ExtractionMode, StructuredOutputStats
from pydantic import BaseModel

from saga.core.models import DocType
from saga.llm import analyzer as analyzer_module
from saga.llm.analyzer import DocumentAnalyzer
from saga.llm.prompts import PromptLibrary
from saga.llm.schemas import (
    DocTypeAssignment,
    ExtractedValueOut,
    FolderPlacement,
    Summary,
    ValueExtraction,
)


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
        mode: Any = None,
        system_prompt: str | None = None,
        callbacks: Any = None,
        fallback_llm_model: Any = None,
        max_primary_retries: int = 3,
        max_fallback_retries: int = 3,
        retry_config: Any = None,
    ) -> tuple[BaseModel | None, StructuredOutputStats]:
        self.calls.append(
            {
                "model": model,
                "fallback": fallback_llm_model,
                "schema": schema.__name__,
                "text": text,
                "mode": mode,
                "system_prompt": system_prompt,
                "callbacks": callbacks,
                "retry_config": retry_config,
            }
        )
        return self.responses.get(schema.__name__), StructuredOutputStats()


def _patch(monkeypatch: pytest.MonkeyPatch, responses: dict[str, BaseModel | None]) -> _Recorder:
    recorder = _Recorder(responses)
    monkeypatch.setattr(analyzer_module, "extract_from_text", recorder)
    return recorder


def _analyzer(**kwargs: Any) -> DocumentAnalyzer:
    return DocumentAnalyzer(cast(Any, object()), PromptLibrary("prompts"), **kwargs)


def _doc_type(name: str, description: str | None = None) -> DocType:
    now = datetime.now(UTC)
    return DocType(
        doc_type_id=name,
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
    )


async def test_classify_doc_type_returns_assignment_and_formats_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(
        monkeypatch,
        {"DocTypeAssignment": DocTypeAssignment(doc_type="invoice", rationale="totals")},
    )
    result = await _analyzer().classify_doc_type(
        title="inv.pdf",
        content="Total due 100 EUR",
        existing_doc_types=[_doc_type("invoice", "a bill"), _doc_type("contract")],
    )
    assert result is not None
    assert result.doc_type == "invoice"

    call = recorder.calls[0]
    assert call["schema"] == "DocTypeAssignment"
    assert call["text"] == "Total due 100 EUR"
    assert call["callbacks"]
    # Title and the existing doc-type catalog are injected into the system prompt.
    assert "inv.pdf" in call["system_prompt"]
    assert "invoice: a bill" in call["system_prompt"]
    assert "contract" in call["system_prompt"]


async def test_classify_doc_type_handles_no_existing_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(monkeypatch, {"DocTypeAssignment": DocTypeAssignment(doc_type="report")})
    await _analyzer().classify_doc_type(title="x", content="y", existing_doc_types=[])
    assert "fresh archive" in recorder.calls[0]["system_prompt"].lower()


async def test_classify_doc_type_returns_none_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, {"DocTypeAssignment": None})
    result = await _analyzer().classify_doc_type(title="x", content="y")
    assert result is None


async def test_summarize_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(monkeypatch, {"Summary": Summary(summary="About taxes.")})
    result = await _analyzer().summarize(filename="t.pdf", content="long body")
    assert result is not None
    assert result.summary == "About taxes."
    call = recorder.calls[0]
    assert call["schema"] == "Summary"
    assert call["text"] == "long body"
    assert "t.pdf" in call["system_prompt"]


async def test_extract_values_returns_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(
        monkeypatch,
        {
            "ValueExtraction": ValueExtraction(
                values=[ExtractedValueOut(key="invoice_number", type="identifier", value="INV-1")]
            )
        },
    )
    result = await _analyzer().extract_values(content="INV-1 total 5 EUR")
    assert result is not None
    assert result.values[0].key == "invoice_number"
    assert recorder.calls[0]["schema"] == "ValueExtraction"
    assert recorder.calls[0]["text"] == "INV-1 total 5 EUR"


async def test_extract_values_uses_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """extract_values must use ExtractionMode.JSON (more reliable for nested list schemas)."""
    recorder = _patch(monkeypatch, {"ValueExtraction": ValueExtraction()})
    await _analyzer().extract_values(content="x")
    assert recorder.calls[0]["mode"] == ExtractionMode.JSON


async def test_classify_doc_type_uses_tool_calling_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool-calling mode is kept for scalar schemas that local models handle reliably."""
    recorder = _patch(monkeypatch, {"DocTypeAssignment": DocTypeAssignment(doc_type="invoice")})
    await _analyzer().classify_doc_type(title="x", content="y")
    assert recorder.calls[0]["mode"] == ExtractionMode.TOOL_CALLING


async def test_place_in_folder_returns_placement_and_injects_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(
        monkeypatch,
        {"FolderPlacement": FolderPlacement(assignments=["f1"], primary="f1")},
    )
    result = await _analyzer().place_in_folder(
        summary="A health insurance letter.",
        doc_type="letter",
        extracted_values="policy_number=P-7",
        folder_tree="- f1 — Insurance — health docs",
        likely_folders="- f1 — Insurance (score 0.90)",
        allow_auto_create=True,
    )
    assert result is not None
    assert result.assignments == ["f1"]
    assert result.primary == "f1"

    call = recorder.calls[0]
    assert call["schema"] == "FolderPlacement"
    # The summary is used as the extraction input text.
    assert call["text"] == "A health insurance letter."
    prompt = call["system_prompt"]
    assert "f1 — Insurance" in prompt
    assert "policy_number=P-7" in prompt
    assert "letter" in prompt
    # allow_auto_create=True permits proposing new folders.
    assert "MAY propose new folders" in prompt


async def test_place_in_folder_respects_allow_auto_create_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(monkeypatch, {"FolderPlacement": FolderPlacement()})
    await _analyzer().place_in_folder(
        summary="s",
        doc_type="letter",
        extracted_values="none",
        folder_tree="(no folders yet)",
        likely_folders="(no similar documents yet)",
        allow_auto_create=False,
    )
    assert "Do NOT create new folders" in recorder.calls[0]["system_prompt"]


async def test_classify_doc_type_truncates_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch(monkeypatch, {"DocTypeAssignment": DocTypeAssignment(doc_type="other")})
    analyzer = _analyzer(max_input_chars=10)
    await analyzer.classify_doc_type(title="t", content="X" * 500)
    assert len(recorder.calls[0]["text"]) == 10


# ---------------------------------------------------------------------------
# Per-step model dispatch
# ---------------------------------------------------------------------------


async def test_extract_uses_global_model_when_no_step_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_model = object()
    recorder = _patch(monkeypatch, {"DocTypeAssignment": DocTypeAssignment(doc_type="x")})
    analyzer = DocumentAnalyzer(cast(Any, global_model), PromptLibrary("prompts"))
    await analyzer.classify_doc_type(title="t", content="c")
    assert recorder.calls[0]["model"] is global_model


async def test_extract_uses_step_model_when_override_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_model = object()
    step_model = object()
    recorder = _patch(monkeypatch, {"DocTypeAssignment": DocTypeAssignment(doc_type="x")})
    analyzer = DocumentAnalyzer(
        cast(Any, global_model),
        PromptLibrary("prompts"),
        step_models={"doc_type": cast(Any, step_model)},
    )
    await analyzer.classify_doc_type(title="t", content="c")
    assert recorder.calls[0]["model"] is step_model


async def test_extract_uses_global_model_for_non_overridden_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_model = object()
    doc_type_model = object()
    recorder = _patch(monkeypatch, {"Summary": Summary(summary="s")})
    analyzer = DocumentAnalyzer(
        cast(Any, global_model),
        PromptLibrary("prompts"),
        step_models={"doc_type": cast(Any, doc_type_model)},
    )
    await analyzer.summarize(filename="f.pdf", content="body")
    # summary step has no override → must use the global model
    assert recorder.calls[0]["model"] is global_model


async def test_extract_uses_step_fallback_when_override_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_fallback = object()
    step_fallback = object()
    recorder = _patch(monkeypatch, {"Summary": Summary(summary="s")})
    analyzer = DocumentAnalyzer(
        cast(Any, object()),
        PromptLibrary("prompts"),
        fallback_model=cast(Any, global_fallback),
        step_fallbacks={"summary": cast(Any, step_fallback)},
    )
    await analyzer.summarize(filename="f.pdf", content="body")
    assert recorder.calls[0]["fallback"] is step_fallback


async def test_extract_uses_global_fallback_for_non_overridden_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_fallback = object()
    recorder = _patch(monkeypatch, {"Summary": Summary(summary="s")})
    analyzer = DocumentAnalyzer(
        cast(Any, object()),
        PromptLibrary("prompts"),
        fallback_model=cast(Any, global_fallback),
        step_fallbacks={"doc_type": cast(Any, object())},
    )
    await analyzer.summarize(filename="f.pdf", content="body")
    assert recorder.calls[0]["fallback"] is global_fallback
