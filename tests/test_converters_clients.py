"""Unit tests for the Docling and Kreuzberg HTTP clients (respx-mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx

from saga.converters.config import OcrConfig, ServiceConfig
from saga.converters.docling import DoclingConverter
from saga.converters.kreuzberg import KreuzbergConverter
from saga.core.errors import ConversionError

DOCLING_URL = "http://docling:5001"
KREUZBERG_URL = "http://kreuzberg:8000"


def _docling(**kwargs: object) -> DoclingConverter:
    return DoclingConverter(
        ServiceConfig(base_url=DOCLING_URL, ocr=OcrConfig(enabled=True, languages=["en"]))
    )


def _kreuzberg() -> KreuzbergConverter:
    return KreuzbergConverter(
        ServiceConfig(base_url=KREUZBERG_URL, ocr=OcrConfig(enabled=True, languages=["eng", "deu"]))
    )


@respx.mock
async def test_docling_convert_success() -> None:
    route = respx.post(f"{DOCLING_URL}/v1/convert/file").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "document": {"md_content": "# Title"}}
        )
    )
    converter = _docling()
    result = await converter.convert(data=b"%PDF", filename="a.pdf", mime_type="application/pdf")
    assert result == "# Title"
    assert route.called
    await converter.aclose()


@respx.mock
async def test_docling_failure_status_raises() -> None:
    respx.post(f"{DOCLING_URL}/v1/convert/file").mock(
        return_value=httpx.Response(200, json={"status": "failure", "errors": ["boom"]})
    )
    converter = _docling()
    with pytest.raises(ConversionError, match="failure"):
        await converter.convert(data=b"x", filename="a.pdf", mime_type="application/pdf")
    await converter.aclose()


@respx.mock
async def test_docling_empty_markdown_raises() -> None:
    respx.post(f"{DOCLING_URL}/v1/convert/file").mock(
        return_value=httpx.Response(200, json={"status": "success", "document": {}})
    )
    converter = _docling()
    with pytest.raises(ConversionError, match="no Markdown"):
        await converter.convert(data=b"x", filename="a.pdf", mime_type="application/pdf")
    await converter.aclose()


@respx.mock
async def test_docling_http_error_raises() -> None:
    respx.post(f"{DOCLING_URL}/v1/convert/file").mock(
        return_value=httpx.Response(500, text="server error")
    )
    converter = _docling()
    with pytest.raises(ConversionError, match="HTTP 500"):
        await converter.convert(data=b"x", filename="a.pdf", mime_type="application/pdf")
    await converter.aclose()


@respx.mock
async def test_kreuzberg_convert_success() -> None:
    route = respx.post(f"{KREUZBERG_URL}/extract").mock(
        return_value=httpx.Response(
            200, json=[{"content": "converted text", "mime_type": "text/markdown"}]
        )
    )
    converter = _kreuzberg()
    result = await converter.convert(data=b"hi", filename="a.docx", mime_type="application/x")
    assert result == "converted text"
    # OCR languages are joined with '+' in the config field.
    request = route.calls.last.request
    assert b"eng+deu" in request.content
    await converter.aclose()


@respx.mock
async def test_kreuzberg_empty_array_raises() -> None:
    respx.post(f"{KREUZBERG_URL}/extract").mock(return_value=httpx.Response(200, json=[]))
    converter = _kreuzberg()
    with pytest.raises(ConversionError, match="empty result"):
        await converter.convert(data=b"x", filename="a.docx", mime_type="application/x")
    await converter.aclose()


@respx.mock
async def test_kreuzberg_error_message_extracted() -> None:
    respx.post(f"{KREUZBERG_URL}/extract").mock(
        return_value=httpx.Response(
            422, json={"error_type": "OcrError", "message": "ocr failed", "status_code": 422}
        )
    )
    converter = _kreuzberg()
    with pytest.raises(ConversionError, match="ocr failed"):
        await converter.convert(data=b"x", filename="a.docx", mime_type="application/x")
    await converter.aclose()


@respx.mock
async def test_transport_error_retries_then_raises() -> None:
    respx.post(f"{KREUZBERG_URL}/extract").mock(side_effect=httpx.ConnectError("refused"))
    converter = KreuzbergConverter(ServiceConfig(base_url=KREUZBERG_URL, max_retries=2))
    with pytest.raises(ConversionError, match="unreachable"):
        await converter.convert(data=b"x", filename="a.docx", mime_type="application/x")
    await converter.aclose()
