"""Unit tests for the prompt library against the real prompt files."""

from __future__ import annotations

import pytest

from docstore.core.errors import ConfigError
from docstore.llm.prompts import PromptLibrary


def test_load_prompt_with_front_matter() -> None:
    lib = PromptLibrary("prompts")
    meta, body = lib.load("analysis/classification.md")
    assert meta.get("id") == "classification"
    assert "classify" in body.lower()


def test_render_prompt_substitutes_variables() -> None:
    lib = PromptLibrary("prompts")
    rendered = lib.render("analysis/classification.md", title="Invoice 1", content="hello")
    assert "Invoice 1" in rendered
    assert "{{" not in rendered


def test_missing_prompt_raises() -> None:
    lib = PromptLibrary("prompts")
    with pytest.raises(ConfigError):
        lib.load("does/not/exist.md")
