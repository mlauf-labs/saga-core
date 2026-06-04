"""Document analysis orchestration (FR-5, FR-14/15/16).

Uses the ``llm-structured-output`` library to extract validated Pydantic models from
the document text via tool-calling, with automatic retries on schema/type errors and
an optional fallback model (FR-18). Instructions are loaded from the externalised
prompts under ``prompts/analysis/*.md`` (NFR-30) and used as system prompts; the
document text is passed as the extraction input. Each step is resilient: a step that
ultimately fails to produce a valid model is logged and falls back to a sensible
default rather than failing the whole document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from llm_structured_output import extract_from_text

from docstore.core.logging import get_logger
from docstore.llm.schemas import (
    AnalysisResult,
    Categorization,
    Classification,
    ValueExtraction,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from docstore.llm.base import ChatModel
    from docstore.llm.prompts import PromptLibrary

_log = get_logger("docstore.llm.analyzer")


class DocumentAnalyzer:
    """Classifies, extracts values from, and categorises a document via an LLM."""

    def __init__(
        self,
        chat_model: ChatModel,
        prompts: PromptLibrary,
        *,
        fallback_model: ChatModel | None = None,
        max_input_chars: int = 12000,
        max_primary_retries: int = 3,
        max_fallback_retries: int = 3,
    ) -> None:
        self._model = chat_model
        self._fallback = fallback_model
        self._prompts = prompts
        self._max_input_chars = max_input_chars
        self._max_primary_retries = max_primary_retries
        self._max_fallback_retries = max_fallback_retries

    def _truncate(self, content: str) -> str:
        return content[: self._max_input_chars]

    async def _extract[ModelT: BaseModel](
        self, *, step: str, schema: type[ModelT], system_prompt: str, text: str
    ) -> ModelT | None:
        """Run one structured-extraction step; return ``None`` on failure (FR-18)."""
        result, stats = await extract_from_text(
            self._model,
            schema,
            text,
            system_prompt=system_prompt,
            fallback_llm_model=self._fallback,
            max_primary_retries=self._max_primary_retries,
            max_fallback_retries=self._max_fallback_retries,
        )
        if result is None:
            _log.warning("analysis_step_failed", step=step, retries=stats.total_retries)
        elif stats.total_retries:
            _log.info("analysis_step_retried", step=step, retries=stats.total_retries)
        return result

    async def classify(self, *, title: str, content: str) -> Classification | None:
        system = self._prompts.render("analysis/classification.md", title=title)
        return await self._extract(
            step="classification",
            schema=Classification,
            system_prompt=system,
            text=self._truncate(content),
        )

    async def extract_values(self, *, content: str) -> ValueExtraction | None:
        system = self._prompts.render("analysis/value-extraction.md")
        return await self._extract(
            step="value_extraction",
            schema=ValueExtraction,
            system_prompt=system,
            text=self._truncate(content),
        )

    async def categorize(
        self, *, content: str, doc_type: str, extracted_values: str
    ) -> Categorization | None:
        system = self._prompts.render(
            "analysis/categorization.md",
            doc_type=doc_type,
            extracted_values=extracted_values,
        )
        return await self._extract(
            step="categorization",
            schema=Categorization,
            system_prompt=system,
            text=self._truncate(content),
        )

    async def analyze(self, *, title: str, content: str) -> AnalysisResult:
        """Run all analysis steps and return the combined, validated result."""
        classification = await self.classify(title=title, content=content)
        doc_type = classification.doc_type if classification else "unknown"

        extraction = await self.extract_values(content=content)
        values = [item.to_model() for item in extraction.values] if extraction else []
        values = [v for v in values if v.key]
        values_summary = ", ".join(f"{v.key}={v.value}" for v in values) or "none"

        categorization = await self.categorize(
            content=content, doc_type=doc_type, extracted_values=values_summary
        )
        paths = [p.strip() for p in categorization.paths if p.strip()] if categorization else []

        _log.info(
            "analysis_done",
            title=title,
            doc_type=doc_type,
            values=len(values),
            categories=len(paths),
        )
        return AnalysisResult(
            doc_type=doc_type,
            extracted_values=values,
            folder_structure=paths,
            category_paths=paths,
        )
