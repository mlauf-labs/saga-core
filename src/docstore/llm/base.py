"""Shared LLM types.

Structured extraction is driven by the ``llm-structured-output`` library against a
LangChain :class:`~langchain_core.language_models.chat_models.BaseChatModel`, so the
chat model is the unit of abstraction (built by :mod:`docstore.llm.providers`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

#: A LangChain chat model usable for tool-calling structured extraction.
type ChatModel = BaseChatModel

__all__ = ["ChatModel"]
