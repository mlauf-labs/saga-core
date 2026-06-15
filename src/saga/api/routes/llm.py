"""LLM helper endpoints for the UI (emoji suggestion).

The API process does not run the ingestion pipeline, so the analyzer is built
lazily on first use from the same LLM configuration the worker uses and cached
for the process lifetime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import EmojiSuggestRequest, EmojiSuggestResponse
from saga.core.errors import ProviderError
from saga.core.logging import get_logger

if TYPE_CHECKING:
    from saga.api.dependencies import Services
    from saga.llm import DocumentAnalyzer

_log = get_logger("saga.api.llm")

router = APIRouter(prefix="/llm", tags=["llm"], dependencies=[AuthDep])

_analyzer: DocumentAnalyzer | None = None
_analyzer_failed = False


def _get_analyzer(services: Services) -> DocumentAnalyzer:
    """Build (once) and return the analyzer, or raise ProviderError when unavailable."""
    global _analyzer, _analyzer_failed
    if _analyzer is not None:
        return _analyzer
    if _analyzer_failed:
        raise ProviderError("The LLM backend is not configured or unavailable.")
    try:
        from saga.llm import (
            DocumentAnalyzer,
            PromptLibrary,
            build_chat_model,
            build_fallback_chat_model,
            build_step_chat_models,
            build_step_fallback_chat_models,
            load_llm_config,
        )

        llm_config = load_llm_config()
        generation = services.config.generation
        _analyzer = DocumentAnalyzer(
            build_chat_model(llm_config),
            PromptLibrary(),
            fallback_model=build_fallback_chat_model(llm_config),
            step_models=build_step_chat_models(llm_config),
            step_fallbacks=build_step_fallback_chat_models(llm_config),
            max_input_chars=llm_config.max_input_chars,
            max_primary_retries=llm_config.max_primary_retries,
            max_fallback_retries=llm_config.max_fallback_retries,
            output_language=generation.language,
            store_description=generation.description,
        )
        _log.info("api_analyzer_ready")
        return _analyzer
    except Exception as exc:
        _analyzer_failed = True
        _log.warning("api_analyzer_unavailable", reason=str(exc))
        raise ProviderError("The LLM backend is not configured or unavailable.") from exc


@router.post(
    "/suggest-emoji",
    response_model=EmojiSuggestResponse,
    summary="Suggest an emoji for a doc-type or folder",
)
async def suggest_emoji(services: ServicesDep, body: EmojiSuggestRequest) -> EmojiSuggestResponse:
    analyzer = _get_analyzer(services)
    kind_label = "document type" if body.kind == "doc_type" else "folder"
    result = await analyzer.suggest_emoji(
        kind=kind_label, name=body.name, description=body.description
    )
    if result is None or not result.emoji.strip():
        raise ProviderError("The LLM did not return an emoji suggestion.")
    return EmojiSuggestResponse(emoji=result.emoji.strip())
