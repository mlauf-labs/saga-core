"""LLM-driven document analysis with structured, retrying extraction (FR-5/14/15/16)."""

from __future__ import annotations

from saga.llm.analyzer import DocumentAnalyzer
from saga.llm.base import ChatModel
from saga.llm.config import LlmConfig, load_llm_config
from saga.llm.prompts import PromptLibrary, render_prompt
from saga.llm.providers import (
    build_chat_model,
    build_fallback_chat_model,
    build_step_chat_models,
    build_step_fallback_chat_models,
)

__all__ = [
    "ChatModel",
    "DocumentAnalyzer",
    "LlmConfig",
    "PromptLibrary",
    "build_chat_model",
    "build_fallback_chat_model",
    "build_step_chat_models",
    "build_step_fallback_chat_models",
    "load_llm_config",
    "render_prompt",
]
