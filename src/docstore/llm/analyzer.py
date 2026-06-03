"""Document analysis orchestration (FR-5, FR-14/15/16).

Renders the externalised prompts (``prompts/analysis/*.md``, NFR-30), calls the
configured LLM provider, and parses + validates the responses into typed models
(FR-18). Malformed responses raise an actionable :class:`AnalysisError`; low-confidence
results are flagged via the per-field ``confidence`` but still stored.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from docstore.core.errors import AnalysisError
from docstore.core.logging import get_logger
from docstore.llm.schemas import (
    AnalysisResult,
    Categorization,
    Classification,
    ValueExtraction,
)

if TYPE_CHECKING:
    from docstore.llm.base import LlmProvider
    from docstore.llm.prompts import PromptLibrary

_log = get_logger("docstore.llm.analyzer")


def _extract_json(text: str) -> str:
    """Return the JSON object substring from a (possibly fenced) LLM response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip a leading ```json / ``` fence and the trailing ```.
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AnalysisError(f"LLM response did not contain a JSON object: {text[:200]!r}")
    return cleaned[start : end + 1]


def _parse[ModelT: BaseModel](text: str, model: type[ModelT]) -> ModelT:
    try:
        payload = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise AnalysisError(
            f"Failed to parse LLM response as JSON for {model.__name__}: {exc}"
        ) from exc
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise AnalysisError(
            f"LLM response did not match the expected {model.__name__} schema: {exc}"
        ) from exc


class DocumentAnalyzer:
    """Classifies, extracts values from, and categorises a document via an LLM."""

    def __init__(
        self, provider: LlmProvider, prompts: PromptLibrary, *, max_input_chars: int = 12000
    ) -> None:
        self._provider = provider
        self._prompts = prompts
        self._max_input_chars = max_input_chars

    def _truncate(self, content: str) -> str:
        return content[: self._max_input_chars]

    async def aclose(self) -> None:
        """Release the underlying provider's network resources."""
        await self._provider.aclose()

    async def classify(self, *, title: str, content: str) -> Classification:
        prompt = self._prompts.render(
            "analysis/classification.md", title=title, content=self._truncate(content)
        )
        return _parse(await self._provider.complete(prompt=prompt), Classification)

    async def extract_values(self, *, content: str) -> ValueExtraction:
        prompt = self._prompts.render(
            "analysis/value-extraction.md", content=self._truncate(content)
        )
        return _parse(await self._provider.complete(prompt=prompt), ValueExtraction)

    async def categorize(
        self, *, content: str, doc_type: str, extracted_values: str
    ) -> Categorization:
        prompt = self._prompts.render(
            "analysis/categorization.md",
            content=self._truncate(content),
            doc_type=doc_type,
            extracted_values=extracted_values,
        )
        return _parse(await self._provider.complete(prompt=prompt), Categorization)

    async def analyze(self, *, title: str, content: str) -> AnalysisResult:
        """Run all analysis steps and return the combined, validated result."""
        classification = await self.classify(title=title, content=content)
        extraction = await self.extract_values(content=content)
        values = [item.to_model() for item in extraction.values]
        values_summary = ", ".join(f"{v.key}={v.value}" for v in values) or "none"
        categorization = await self.categorize(
            content=content,
            doc_type=classification.doc_type,
            extracted_values=values_summary,
        )
        paths = [p.strip() for p in categorization.paths if p.strip()]
        _log.info(
            "analysis_done",
            title=title,
            doc_type=classification.doc_type,
            values=len(values),
            categories=len(paths),
        )
        return AnalysisResult(
            doc_type=classification.doc_type,
            extracted_values=values,
            folder_structure=paths,
            category_paths=paths,
        )
