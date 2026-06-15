"""Unit tests for converter routing (FR-3)."""

from __future__ import annotations

import pytest

from saga.converters.router import ConverterRouter, RoutingConfig


@pytest.fixture
def router() -> ConverterRouter:
    routing = RoutingConfig(
        default="kreuzberg",
        by_extension={"pdf": "docling"},
        by_mime_type={"application/pdf": "docling"},
    )
    return ConverterRouter(routing)


def test_pdf_extension_routes_to_docling(router: ConverterRouter) -> None:
    assert router.resolve(filename="invoice.pdf") == "docling"


def test_pdf_mime_routes_to_docling(router: ConverterRouter) -> None:
    assert router.resolve(filename="scan", mime_type="application/pdf") == "docling"


def test_other_formats_fall_back_to_kreuzberg(router: ConverterRouter) -> None:
    assert router.resolve(filename="report.docx") == "kreuzberg"
    assert router.resolve(filename="photo.png", mime_type="image/png") == "kreuzberg"


def test_extension_is_case_insensitive(router: ConverterRouter) -> None:
    assert router.resolve(filename="DOC.PDF") == "docling"
