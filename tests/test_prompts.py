"""Unit tests for prompt rendering (NFR-30)."""

from __future__ import annotations

import pytest

from docstore.core.errors import ConfigError
from docstore.llm.prompts import render_prompt


def test_render_replaces_placeholders() -> None:
    out = render_prompt("Title: {{ title }}", title="Invoice")
    assert out == "Title: Invoice"


def test_render_missing_variable_raises() -> None:
    with pytest.raises(ConfigError):
        render_prompt("{{ missing }}")


def test_render_multiple_placeholders() -> None:
    out = render_prompt("{{ a }}-{{ b }}", a="1", b="2")
    assert out == "1-2"
