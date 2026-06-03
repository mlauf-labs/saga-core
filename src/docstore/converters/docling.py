"""Docling HTTP client (default converter for PDFs, FR-3/FR-4).

Uses docling-serve's multipart file endpoint ``POST /v1/convert/file`` and reads the
Markdown from ``document.md_content`` in the JSON response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docstore.converters.base import HttpConverter
from docstore.core.errors import ConversionError
from docstore.core.logging import get_logger

if TYPE_CHECKING:
    import httpx

_log = get_logger("docstore.converters.docling")

_CONVERT_PATH = "/v1/convert/file"


class DoclingConverter(HttpConverter):
    """Converts documents to Markdown via a docling-serve instance."""

    name = "docling"

    def _form_fields(self) -> dict[str, str | list[str]]:
        fields: dict[str, str | list[str]] = {
            "to_formats": "md",
            "do_ocr": str(self._config.ocr.enabled).lower(),
            "image_export_mode": "placeholder",
            "table_mode": "accurate",
        }
        if self._config.ocr.enabled and self._config.ocr.languages:
            fields["ocr_lang"] = list(self._config.ocr.languages)
        return fields

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._config.api_key} if self._config.api_key else {}

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        files = {"files": (filename, data, mime_type or "application/octet-stream")}

        async def _request() -> httpx.Response:
            return await self.client.post(
                _CONVERT_PATH,
                data=self._form_fields(),
                files=files,
                headers=self._headers(),
            )

        response = await self._post_with_retry(_request)
        if response.status_code != 200:
            raise ConversionError(
                f"docling: conversion of '{filename}' failed with HTTP "
                f"{response.status_code}: {response.text[:500]}"
            )
        payload: dict[str, Any] = response.json()
        status = payload.get("status", "failure")
        if status == "failure":
            errors = payload.get("errors") or ["unknown error"]
            raise ConversionError(
                f"docling: conversion of '{filename}' returned status 'failure': {errors}"
            )
        markdown = (payload.get("document") or {}).get("md_content")
        if not markdown:
            raise ConversionError(
                f"docling: conversion of '{filename}' produced no Markdown content "
                f"(status='{status}')."
            )
        _log.info("converted", converter=self.name, filename=filename, status=status)
        return str(markdown)
