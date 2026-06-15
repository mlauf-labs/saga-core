"""Document analysis orchestration (FR-5, FR-14/15/16).

Uses the ``saidex`` library to extract validated Pydantic models from
the document text via tool-calling, with automatic retries on schema/type errors and
an optional fallback model (FR-18). Instructions are loaded from the externalised
prompts under ``prompts/analysis/*.md`` (NFR-30) and used as system prompts; the
document text is passed as the extraction input. Each step is resilient: a step that
ultimately fails to produce a valid model is logged and falls back to a sensible
default rather than failing the whole document.

The analyzer exposes the individual steps the pipeline drives in order: doc-type
classification, value extraction, summarisation, and folder placement.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
import openai
from pydantic import Field, create_model
from saidex import ExtractionMode, RetryConfig, extract_from_text, extract_with_tools

from saga.core.logging import get_logger
from saga.llm.callbacks import LlmCallLogger
from saga.llm.schemas import (
    DocTypeAssignment,
    EmojiSuggestion,
    FolderDecision,
    FolderPlacement,
    Summary,
    ValueExtraction,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from saga.core.models import DocType
    from saga.llm.base import ChatModel
    from saga.llm.folder_tools import Tool
    from saga.llm.prompts import PromptLibrary
    from saga.storage import PostgresStore

_log = get_logger("saga.llm.analyzer")

#: Network-retry coverage widened over the saidex default: ``httpx.TransportError``
#: additionally covers ``ReadError`` — an Ollama fleet member dying mid-stream —
#: so the retry lands the next attempt on a healthy server instead of failing
#: the extraction step.
_NETWORK_RETRY = RetryConfig(
    retryable_exceptions=(
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
    )
)


class DocumentAnalyzer:
    """Runs the LLM analysis steps for a document (typing, values, summary, placement)."""

    def __init__(
        self,
        chat_model: ChatModel,
        prompts: PromptLibrary,
        *,
        fallback_model: ChatModel | None = None,
        step_models: dict[str, ChatModel] | None = None,
        step_fallbacks: dict[str, ChatModel] | None = None,
        max_input_chars: int = 12000,
        max_primary_retries: int = 3,
        max_fallback_retries: int = 3,
        max_doctypes_in_prompt: int = 100,
        max_folders_in_prompt: int = 200,
        output_language: str = "English",
        store_description: str = "",
        custom_doctype_instructions: str = "",
        custom_metadata_instructions: str = "",
        custom_summary_instructions: str = "",
        custom_folder_instructions: str = "",
    ) -> None:
        self._model = chat_model
        self._fallback = fallback_model
        # Per-step overrides; steps absent from these dicts fall back to the globals.
        self._step_models: dict[str, ChatModel] = step_models or {}
        self._step_fallbacks: dict[str, ChatModel] = step_fallbacks or {}
        self._prompts = prompts
        self._max_input_chars = max_input_chars
        self._max_primary_retries = max_primary_retries
        self._max_fallback_retries = max_fallback_retries
        self._max_doctypes_in_prompt = max_doctypes_in_prompt
        self._max_folders_in_prompt = max_folders_in_prompt
        # Language for all generated text except value-extraction keys (always English).
        self._output_language = output_language
        # Optional archive description injected into LLM prompts as contextual guidance.
        self._store_description = store_description
        # Optional per-step custom instructions from config/prompts/*.md.
        self._custom_doctype_instructions = custom_doctype_instructions
        self._custom_metadata_instructions = custom_metadata_instructions
        self._custom_summary_instructions = custom_summary_instructions
        self._custom_folder_instructions = custom_folder_instructions

    def _truncate(self, content: str) -> str:
        return content[: self._max_input_chars]

    def _format_doc_types(self, doc_types: list[DocType] | None) -> str:
        """Render existing doc-types (name + description) as a bounded bullet list."""
        items = doc_types or []
        if not items:
            return "(none yet - this is a fresh archive; coin sensible new types)"
        shown = items[: self._max_doctypes_in_prompt]
        lines = [f"- {dt.name}: {dt.description or 'no description'}" for dt in shown]
        if len(items) > len(shown):
            lines.append(f"- ... ({len(items) - len(shown)} more)")
        return "\n".join(lines)

    @property
    def _store_context(self) -> str:
        """Formatted markdown block injected into prompts when a store description is set."""
        if not self._store_description:
            return ""
        return (
            "## Archive context\n\n"
            f"{self._store_description}\n\n"
            "Consider this context when generating folder names, doc-type labels, "
            "descriptions, and titles.\n"
        )

    @property
    def _doctype_instructions_section(self) -> str:
        """Custom doc-type instructions block, empty string when not configured."""
        if not self._custom_doctype_instructions:
            return ""
        return f"## Additional instructions\n\n{self._custom_doctype_instructions}\n\n"

    @property
    def _metadata_instructions_section(self) -> str:
        """Custom metadata extraction instructions block, empty string when not configured."""
        if not self._custom_metadata_instructions:
            return ""
        return f"## Additional instructions\n\n{self._custom_metadata_instructions}\n\n"

    @property
    def _summary_instructions_section(self) -> str:
        """Custom summary instructions block, empty string when not configured."""
        if not self._custom_summary_instructions:
            return ""
        return f"## Additional instructions\n\n{self._custom_summary_instructions}\n\n"

    @property
    def _folder_instructions_section(self) -> str:
        """Custom folder placement instructions block, empty string when not configured."""
        if not self._custom_folder_instructions:
            return ""
        return f"## Additional instructions\n\n{self._custom_folder_instructions}\n\n"

    async def _extract[ModelT: BaseModel](
        self,
        *,
        step: str,
        schema: type[ModelT],
        system_prompt: str,
        text: str,
        mode: ExtractionMode = ExtractionMode.TOOL_CALLING,
        trace_callbacks: list[Any] | None = None,
    ) -> ModelT | None:
        """Run one structured-extraction step; return ``None`` on failure (FR-18)."""
        model = self._step_models.get(step, self._model)
        fallback: ChatModel | None = self._step_fallbacks.get(step, self._fallback)
        callback = LlmCallLogger(step)
        all_callbacks: list[Any] = [callback, *(trace_callbacks or [])]
        _log.info("analysis_step_start", step=step, chars=len(text), mode=mode.value)
        started = time.monotonic()
        result, stats = await extract_from_text(
            model,
            schema,
            text,
            mode=mode,
            system_prompt=system_prompt,
            callbacks=all_callbacks,
            fallback_llm_model=fallback,
            max_primary_retries=self._max_primary_retries,
            max_fallback_retries=self._max_fallback_retries,
            retry_config=_NETWORK_RETRY,
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

    async def classify_doc_type(
        self,
        *,
        title: str,
        content: str,
        existing_doc_types: list[DocType] | None = None,
        trace_callbacks: list[Any] | None = None,
    ) -> DocTypeAssignment | None:
        """Classify the document into one (existing or new) doc-type (FR-14)."""
        system = self._prompts.render(
            "analysis/doc-type.md",
            title=title,
            existing_doc_types=self._format_doc_types(existing_doc_types),
            output_language=self._output_language,
            store_context=self._store_context,
            doctype_instructions=self._doctype_instructions_section,
        )
        return await self._extract(
            step="doc_type",
            schema=DocTypeAssignment,
            system_prompt=system,
            text=self._truncate(content),
            trace_callbacks=trace_callbacks,
        )

    async def suggest_emoji(
        self,
        *,
        kind: str,
        name: str,
        description: str | None = None,
        trace_callbacks: list[Any] | None = None,
    ) -> EmojiSuggestion | None:
        """Suggest a single emoji for a doc-type or folder (UI helper).

        Mirrors the emoji guidance used during doc-type classification and
        folder creation so suggestions are consistent with the pipeline.
        """
        system = self._prompts.render(
            "analysis/emoji-suggestion.md",
            kind=kind,
            store_context=self._store_context,
        )
        text = f"Name: {name}\nDescription: {description or '(no description)'}"
        return await self._extract(
            step="emoji_suggestion",
            schema=EmojiSuggestion,
            system_prompt=system,
            text=text,
            trace_callbacks=trace_callbacks,
        )

    async def extract_values(
        self, *, content: str, trace_callbacks: list[Any] | None = None
    ) -> ValueExtraction | None:
        """Extract identifiers and numeric values (FR-15).

        Uses JSON mode instead of tool-calling because smaller local models
        (e.g. llama3.1:8b) struggle to format a deeply-nested list inside a
        tool call.  In JSON mode the model simply emits a plain JSON object
        ``{"values": [...]}`` which is much more reliable.
        """
        system = self._prompts.render(
            "analysis/value-extraction.md",
            metadata_instructions=self._metadata_instructions_section,
        )
        return await self._extract(
            step="value_extraction",
            schema=ValueExtraction,
            system_prompt=system,
            text=self._truncate(content),
            mode=ExtractionMode.JSON,
            trace_callbacks=trace_callbacks,
        )

    async def summarize(
        self, *, filename: str, content: str, trace_callbacks: list[Any] | None = None
    ) -> Summary | None:
        """Produce a short title and high-level summary for the document (FR-14).

        The ``filename`` is passed as a hint to the LLM so it can coin a
        meaningful human-readable title; it is not copied verbatim.
        """
        system = self._prompts.render(
            "analysis/summary.md",
            filename=filename,
            output_language=self._output_language,
            store_context=self._store_context,
            summary_instructions=self._summary_instructions_section,
        )
        return await self._extract(
            step="summary",
            schema=Summary,
            system_prompt=system,
            text=self._truncate(content),
            trace_callbacks=trace_callbacks,
        )

    async def place_in_folder(
        self,
        *,
        summary: str,
        doc_type: str,
        extracted_values: str,
        folder_tree: str,
        likely_folders: str,
        allow_auto_create: bool,
        trace_callbacks: list[Any] | None = None,
    ) -> FolderPlacement | None:
        """Choose 1..n folders (and optionally new ones) for the document (FR-16)."""
        creation_rule = (
            "If no existing folder fits well, you MAY propose new folders in "
            "'new_folders'."
            if allow_auto_create
            else "Do NOT create new folders; choose only from the existing folder ids."
        )
        system = self._prompts.render(
            "analysis/folder-placement.md",
            doc_type=doc_type,
            extracted_values=extracted_values,
            folder_tree=folder_tree,
            likely_folders=likely_folders,
            creation_rule=creation_rule,
            output_language=self._output_language,
            store_context=self._store_context,
            folder_instructions=self._folder_instructions_section,
        )
        # The summary is the most compact representation of the document's topic.
        return await self._extract(
            step="folder_placement",
            schema=FolderPlacement,
            system_prompt=system,
            text=summary,
            trace_callbacks=trace_callbacks,
        )

    async def place_in_folder_agentic(
        self,
        *,
        summary: str,
        doc_type: str,
        extracted_values: str,
        folder_tree: str,
        likely_folders: str,
        allow_auto_create: bool,
        db: PostgresStore,
        trace_callbacks: list[Any] | None = None,
    ) -> FolderDecision | None:
        """Place the document using an agentic tool-loop (FR-16/17).

        The LLM may call ``create_folder`` any number of times to build the
        required hierarchy, then submits a ``FolderDecision`` as its final
        answer.  Only the deepest/most specific folder id ends up in
        ``assignments`` — ancestor folders are created but not assigned,
        fixing the "document in every hierarchy level" bug.

        Falls back gracefully to ``None`` on failure so the pipeline can
        continue without placement rather than aborting ingestion (FR-18).
        """
        from saga.llm.folder_tools import build_folder_tools

        creation_rule = (
            "You MAY call create_folder to create new folders if none of the "
            "existing ones fit well."
            if allow_auto_create
            else "Do NOT create new folders; choose only from the existing folder ids."
        )
        system = self._prompts.render(
            "analysis/folder-agent.md",
            doc_type=doc_type,
            extracted_values=extracted_values,
            folder_tree=folder_tree,
            likely_folders=likely_folders,
            creation_rule=creation_rule,
            output_language=self._output_language,
            store_context=self._store_context,
            folder_instructions=self._folder_instructions_section,
        )

        model = self._step_models.get("folder_placement_agentic", self._model)
        fallback: ChatModel | None = self._step_fallbacks.get(
            "folder_placement_agentic", self._fallback
        )
        tools: list[Tool] = build_folder_tools(db) if allow_auto_create else []
        callback = LlmCallLogger("folder_placement_agentic")
        all_callbacks: list[Any] = [callback, *(trace_callbacks or [])]

        _log.info("analysis_step_start", step="folder_placement_agentic", chars=len(summary))
        started = time.monotonic()

        result, stats = await extract_with_tools(
            model,
            FolderDecision,
            summary,
            tools=tools,
            system_prompt=system,
            callbacks=all_callbacks,
            fallback_llm_model=fallback,
            max_iterations=self._max_primary_retries * 4,  # generous budget for tool calls
            max_validation_retries=self._max_primary_retries,
            retry_config=_NETWORK_RETRY,
        )

        elapsed_ms = round((time.monotonic() - started) * 1000)
        if result is None:
            _log.warning(
                "analysis_step_failed",
                step="folder_placement_agentic",
                llm_calls=callback.calls,
                iterations=stats.iterations,
                tool_calls=stats.tool_calls,
                validation_retries=stats.validation_retries,
                fallback_used=stats.fallback_used,
                elapsed_ms=elapsed_ms,
            )
        else:
            _log.info(
                "analysis_step_done",
                step="folder_placement_agentic",
                llm_calls=callback.calls,
                iterations=stats.iterations,
                tool_calls=stats.tool_calls,
                validation_retries=stats.validation_retries,
                fallback_used=stats.fallback_used,
                elapsed_ms=elapsed_ms,
            )
        return result

    async def extract_fields(
        self,
        *,
        content: str,
        fields: dict[str, str],
        trace_callbacks: list[Any] | None = None,
    ) -> dict[str, str | None]:
        """Extract a caller-defined set of fields from a document.

        Builds a dynamic Pydantic schema from *fields* (key → description) so the
        LLM knows exactly what to extract for each column.  Uses JSON mode for
        reliability with smaller local models (same rationale as ``extract_values``).

        Args:
            content: The document's Markdown text.
            fields: Mapping of field key → human description of what to extract.
            trace_callbacks: Optional LangChain callbacks for tracing.

        Returns:
            A dict mapping each field key to the extracted string value, or ``None``
            when the LLM could not find a value for that key.  On total extraction
            failure an all-``None`` dict is returned so the caller can still build a
            table row.
        """
        field_definitions: dict[str, Any] = {
            key: (str | None, Field(default=None, description=desc))
            for key, desc in fields.items()
        }
        DynamicSchema = create_model("FieldExtractionResult", **field_definitions)

        bullets = "\n".join(f"- `{k}`: {v}" for k, v in fields.items())
        system = self._prompts.render("analysis/field-extraction.md", fields=bullets)

        result = await self._extract(
            step="field_extraction_custom",
            schema=DynamicSchema,
            system_prompt=system,
            text=self._truncate(content),
            mode=ExtractionMode.JSON,
            trace_callbacks=trace_callbacks,
        )
        if result is None:
            return dict.fromkeys(fields)
        return {k: getattr(result, k, None) for k in fields}
