"""Kreuzberg HTTP client (default converter for non-PDF formats + OCR, FR-3/FR-4).

Uses Kreuzberg's ``POST /extract`` multipart endpoint with ``output_format=markdown``
and reads ``content`` from the first element of the returned JSON array.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from docstore.converters.base import HttpConverter
from docstore.core.errors import ConversionError
from docstore.core.logging import get_logger

if TYPE_CHECKING:
    import httpx

_log = get_logger("docstore.converters.kreuzberg")

_EXTRACT_PATH = "/extract"


class KreuzbergConverter(HttpConverter):
    """Converts documents to Markdown via a Kreuzberg API instance."""

    name = "kreuzberg"

    def _form_fields(self) -> dict[str, str]:
        fields: dict[str, str] = {"output_format": "markdown"}
        if self._config.ocr.enabled and self._config.ocr.languages:
            ocr_config = {"ocr": {"language": "+".join(self._config.ocr.languages)}}
            fields["config"] = json.dumps(ocr_config)
        return fields

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        files = {"files": (filename, data, mime_type or "application/octet-stream")}

        async def _request() -> httpx.Response:
            return await self.client.post(_EXTRACT_PATH, data=self._form_fields(), files=files)

        response = await self._post_with_retry(_request)
        if response.status_code != 200:
            message = response.text[:500]
            try:
                body = response.json()
                message = body.get("message", message)
            except (ValueError, json.JSONDecodeError):
                pass
            raise ConversionError(
                f"kreuzberg: conversion of '{filename}' failed with HTTP "
                f"{response.status_code}: {message}"
            )
        payload: list[dict[str, Any]] = response.json()
        if not payload:
            raise ConversionError(
                f"kreuzberg: conversion of '{filename}' returned an empty result."
            )
        content = payload[0].get("content")
        if not content:
            raise ConversionError(f"kreuzberg: conversion of '{filename}' produced no content.")
        _log.info("converted", converter=self.name, filename=filename)
        return str(content)
