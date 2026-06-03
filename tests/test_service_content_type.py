"""Unit tests for MIME-type resolution on upload (robustness, FR-2)."""

from __future__ import annotations

from docstore.api.service import resolve_content_type


def test_keeps_specific_content_type() -> None:
    assert resolve_content_type("application/pdf", "x.pdf") == "application/pdf"


def test_guesses_from_extension_when_generic() -> None:
    assert resolve_content_type("application/octet-stream", "invoice.txt") == "text/plain"
    assert resolve_content_type("", "page.html") == "text/html"


def test_falls_back_when_unknown() -> None:
    assert resolve_content_type(None, "data.unknownext") == "application/octet-stream"
