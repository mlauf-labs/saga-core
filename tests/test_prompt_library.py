"""Unit tests for the prompt library against the real prompt files."""

from __future__ import annotations

import pytest

from saga.core.errors import ConfigError
from saga.llm.prompts import PromptLibrary


def test_load_prompt_with_front_matter() -> None:
    lib = PromptLibrary("prompts")
    meta, body = lib.load("mcp/get_document.md")
    assert meta.get("tool") == "get_document"
    assert "document" in body.lower()


def test_render_prompt_substitutes_variables() -> None:
    lib = PromptLibrary("prompts")
    rendered = lib.render(
        "analysis/summary.md",
        filename="invoice_2026_01.pdf",
        output_language="English",
        store_context="A personal document archive.",
        summary_instructions="",
    )
    assert "invoice_2026_01.pdf" in rendered
    assert "{{" not in rendered


def test_missing_prompt_raises() -> None:
    lib = PromptLibrary("prompts")
    with pytest.raises(ConfigError):
        lib.load("does/not/exist.md")
