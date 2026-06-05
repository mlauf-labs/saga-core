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

import time
from typing import TYPE_CHECKING

from llm_structured_output import extract_from_text

from docstore.core.logging import get_logger
from docstore.llm.callbacks import LlmCallLogger
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
        max_categories_in_prompt: int = 200,
    ) -> None:
        self._model = chat_model
        self._fallback = fallback_model
        self._prompts = prompts
        self._max_input_chars = max_input_chars
        self._max_primary_retries = max_primary_retries
        self._max_fallback_retries = max_fallback_retries
        self._max_categories_in_prompt = max_categories_in_prompt

    def _truncate(self, content: str) -> str:
        return content[: self._max_input_chars]

    def _format_categories(self, existing_categories: list[str] | None) -> str:
        """Render existing category paths as a bounded bullet list for the prompt."""
        paths = [p for p in (existing_categories or []) if p.strip()]
        if not paths:
            return "(none yet - this is a fresh archive; create sensible new folders)"
        shown = paths[: self._max_categories_in_prompt]
        lines = [f"- {path}" for path in shown]
        if len(paths) > len(shown):
            lines.append(f"- ... ({len(paths) - len(shown)} more)")
        return "\n".join(lines)

    async def _extract[ModelT: BaseModel](
        self, *, step: str, schema: type[ModelT], system_prompt: str, text: str
    ) -> ModelT | None:
        """Run one structured-extraction step; return ``None`` on failure (FR-18).

        Logs the step duration, the number of LLM calls, the validation-retry count
        and whether the fallback model was used, plus (via the callback) the exact
        correction text sent back to the model on each retry (NFR-16).
        """
        callback = LlmCallLogger(step)
        _log.info("analysis_step_start", step=step, chars=len(text))
        started = time.monotonic()
        result, stats = await extract_from_text(
            self._model,
            schema,
            text,
            system_prompt=system_prompt,
            callbacks=[callback],
            fallback_llm_model=self._fallback,
            max_primary_retries=self._max_primary_retries,
            max_fallback_retries=self._max_fallback_retries,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if result is None:
            _log.warning(
                "analysis_step_failed",
                step=step,
                llm_calls=callback.calls,
                validation_retries=stats.total_retries,
                fallback_used=stats.fallback_used,
                elapsed_ms=elapsed_ms,
            )
        else:
            _log.info(
                "analysis_step_done",
                step=step,
                llm_calls=callback.calls,
                validation_retries=stats.total_retries,
                fallback_used=stats.fallback_used,
                elapsed_ms=elapsed_ms,
            )
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
        self,
        *,
        content: str,
        doc_type: str,
        extracted_values: str,
        existing_categories: list[str] | None = None,
    ) -> Categorization | None:
        system = self._prompts.render(
            "analysis/categorization.md",
            doc_type=doc_type,
            extracted_values=extracted_values,
            existing_categories=self._format_categories(existing_categories),
        )
        return await self._extract(
            step="categorization",
            schema=Categorization,
            system_prompt=system,
            text=self._truncate(content),
        )

    async def analyze(
        self, *, title: str, content: str, existing_categories: list[str] | None = None
    ) -> AnalysisResult:
        """Run all analysis steps and return the combined, validated result.

        ``existing_categories`` are the folder paths already present in the archive;
        they are shown to the model so it can place the document into the established
        structure or extend it consistently (FR-16).
        """
        classification = await self.classify(title=title, content=content)
        doc_type = classification.doc_type if classification else "unknown"

        extraction = await self.extract_values(content=content)
        values = [item.to_model() for item in extraction.values] if extraction else []
        values = [v for v in values if v.key]
        values_summary = ", ".join(f"{v.key}={v.value}" for v in values) or "none"

        categorization = await self.categorize(
            content=content,
            doc_type=doc_type,
            extracted_values=values_summary,
            existing_categories=existing_categories,
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
