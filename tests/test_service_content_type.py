"""Unit tests for filename/MIME-type resolution on upload (robustness, FR-2)."""

from __future__ import annotations

from saga.api.service import decode_filename, resolve_content_type


def test_keeps_specific_content_type() -> None:
    assert resolve_content_type("application/pdf", "x.pdf") == "application/pdf"


def test_guesses_from_extension_when_generic() -> None:
    assert resolve_content_type("application/octet-stream", "invoice.txt") == "text/plain"
    assert resolve_content_type("", "page.html") == "text/html"


def test_falls_back_when_unknown() -> None:
    assert resolve_content_type(None, "data.unknownext") == "application/octet-stream"


def test_decode_filename_passthrough_for_plain_names() -> None:
    assert decode_filename("Brutto-Netto-Abrechnung 2025 11 November.pdf") == (
        "Brutto-Netto-Abrechnung 2025 11 November.pdf"
    )


def test_decode_filename_decodes_rfc2047_encoded_word() -> None:
    encoded = "=?utf-8?B?QnJ1dHRvLU5ldHRvLUFicmVjaG51bmcgMjAyNSAxMSBOb3ZlbWJlci5wZGY=?="
    assert decode_filename(encoded) == "Brutto-Netto-Abrechnung 2025 11 November.pdf"


def test_decode_filename_then_mime_guess_recovers_pdf() -> None:
    encoded = "=?utf-8?B?QnJ1dHRvLU5ldHRvLUFicmVjaG51bmcgMjAyNSAxMSBOb3ZlbWJlci5wZGY=?="
    name = decode_filename(encoded)
    assert resolve_content_type("application/octet-stream", name) == "application/pdf"


def test_decode_filename_returns_input_on_garbage() -> None:
    assert decode_filename("=?bogus?X?zz?=") == "=?bogus?X?zz?="
