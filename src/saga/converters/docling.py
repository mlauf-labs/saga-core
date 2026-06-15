"""Docling HTTP client (default converter for PDFs, FR-3/FR-4).

Uses docling-serve's multipart file endpoint ``POST /v1/convert/file`` (sync) or
``POST /v1/convert/file/async`` + ``GET /v1/status/poll/{task_id}`` + ``GET /v1/result/{task_id}``
(async, used for VLM pipeline which takes longer than a single HTTP timeout allows).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from saga.converters.base import HttpConverter
from saga.core.errors import ConversionError
from saga.core.logging import get_logger

if TYPE_CHECKING:
    import httpx

_log = get_logger("saga.converters.docling")

_CONVERT_SYNC_PATH = "/v1/convert/file"
_CONVERT_ASYNC_PATH = "/v1/convert/file/async"
_POLL_PATH = "/v1/status/poll/{task_id}"
_RESULT_PATH = "/v1/result/{task_id}"

_POLL_WAIT_SECONDS = 5      # server-side long-poll hint (only effective for pending→started)
_POLL_INTERVAL_SECONDS = 5  # client-side sleep between polls
_POLL_MAX_ATTEMPTS = 300    # 300 × 5 s = up to 25 minutes (covers the 20-min VLM timeout)


class DoclingConverter(HttpConverter):
    """Converts documents to Markdown via a docling-serve instance."""

    name = "docling"

    def _form_fields(self) -> dict[str, str | list[str]]:
        vlm = self._config.vlm
        if vlm.enabled:
            return self._vlm_form_fields(vlm)
        return self._ocr_form_fields()

    def _vlm_form_fields(self, vlm: object) -> dict[str, str | list[str]]:
        """Build form fields for the VLM pipeline.

        Three modes are supported (set via ``vlm.mode`` in converters.yaml):

        * ``api``    – External OpenAI-compatible endpoint (Ollama, vLLM, …).
        * ``preset`` – Built-in docling-serve preset (model inside container).
        * ``local``  – HuggingFace model loaded inline in the container (CPU/GPU).
        """
        import json as _json

        base: dict[str, str | list[str]] = {
            "to_formats": "md",
            "pipeline": "vlm",
            "image_export_mode": "placeholder",
            "table_mode": "accurate",
        }

        mode = getattr(vlm, "mode", "api")

        if mode == "preset":
            base["vlm_pipeline_preset"] = vlm.preset  # type: ignore[attr-defined]
            return base

        if mode == "local":
            base["vlm_pipeline_model_local"] = _json.dumps({
                "repo_id": vlm.repo_id,  # type: ignore[attr-defined]
                "inference_framework": vlm.inference_framework,  # type: ignore[attr-defined]
                "transformers_model_type": vlm.transformers_model_type,  # type: ignore[attr-defined]
                "max_new_tokens": vlm.max_new_tokens,  # type: ignore[attr-defined]
                "load_in_8bit": vlm.load_in_8bit,  # type: ignore[attr-defined]
                "prompt": vlm.prompt,  # type: ignore[attr-defined]
                "response_format": vlm.response_format,  # type: ignore[attr-defined]
                "scale": vlm.scale,  # type: ignore[attr-defined]
                "temperature": vlm.temperature,  # type: ignore[attr-defined]
                "extra_generation_config": {"skip_special_tokens": False},
            })
            return base

        # mode == "api" (default)
        base["vlm_pipeline_model_api"] = _json.dumps({
            "url": vlm.api_url,  # type: ignore[attr-defined]
            "headers": {},
            "params": {"model": vlm.model},  # type: ignore[attr-defined]
            "timeout": vlm.timeout,  # type: ignore[attr-defined]
            "concurrency": vlm.concurrency,  # type: ignore[attr-defined]
            "prompt": vlm.prompt,  # type: ignore[attr-defined]
            "scale": vlm.scale,  # type: ignore[attr-defined]
            "response_format": vlm.response_format,  # type: ignore[attr-defined]
            "temperature": vlm.temperature,  # type: ignore[attr-defined]
        })
        return base

    def _ocr_form_fields(self) -> dict[str, str | list[str]]:
        """Build form fields for the standard OCR pipeline (no VLM)."""
        fields: dict[str, str | list[str]] = {
            "to_formats": "md",
            "do_ocr": str(self._config.ocr.enabled).lower(),
            "force_ocr": str(self._config.ocr.force).lower(),
            "ocr_preset": self._config.ocr.preset,
            "image_export_mode": "placeholder",
            "table_mode": "accurate",
        }
        if self._config.ocr.enabled and self._config.ocr.languages:
            fields["ocr_lang"] = list(self._config.ocr.languages)
        return fields

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._config.api_key} if self._config.api_key else {}

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        if self._config.vlm.enabled:
            return await self._convert_async(data=data, filename=filename, mime_type=mime_type)
        return await self._convert_sync(data=data, filename=filename, mime_type=mime_type)

    # ------------------------------------------------------------------
    # Sync path (OCR / standard pipeline)
    # ------------------------------------------------------------------

    async def _convert_sync(self, *, data: bytes, filename: str, mime_type: str) -> str:
        files = {"files": (filename, data, mime_type or "application/octet-stream")}

        async def _request() -> httpx.Response:
            return await self.client.post(
                _CONVERT_SYNC_PATH,
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
        return self._extract_markdown(response.json(), filename)

    # ------------------------------------------------------------------
    # Async path (VLM pipeline — can take minutes per page)
    # ------------------------------------------------------------------

    async def _convert_async(self, *, data: bytes, filename: str, mime_type: str) -> str:
        files = {"files": (filename, data, mime_type or "application/octet-stream")}

        # 1. Submit the async task.
        async def _submit() -> httpx.Response:
            return await self.client.post(
                _CONVERT_ASYNC_PATH,
                data=self._form_fields(),
                files=files,
                headers=self._headers(),
            )

        submit_response = await self._post_with_retry(_submit)
        if submit_response.status_code not in (200, 202):
            raise ConversionError(
                f"docling VLM: submission of '{filename}' failed with HTTP "
                f"{submit_response.status_code}: {submit_response.text[:500]}"
            )

        task_id: str | None = None
        try:
            body = submit_response.json()
            task_id = body.get("task_id") or body.get("id")
        except Exception:
            pass
        if not task_id:
            raise ConversionError(
                f"docling VLM: async submission did not return a task_id for '{filename}'. "
                f"Response: {submit_response.text[:300]}"
            )

        _log.info(
            "vlm_task_submitted",
            converter=self.name,
            filename=filename,
            task_id=task_id,
        )

        # 2. Long-poll until the task finishes.
        poll_url = _POLL_PATH.format(task_id=task_id)
        result_url = _RESULT_PATH.format(task_id=task_id)

        for attempt in range(_POLL_MAX_ATTEMPTS):
            if attempt > 0:
                # Real sleep between polls — the server-side `wait` param only gates
                # the pending→started transition, not the started→success transition.
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            poll_response = await self.client.get(
                f"{poll_url}?wait={_POLL_WAIT_SECONDS}",
                headers=self._headers(),
                timeout=_POLL_WAIT_SECONDS + 10,
            )
            if poll_response.status_code != 200:
                raise ConversionError(
                    f"docling VLM: poll for task {task_id} returned HTTP "
                    f"{poll_response.status_code}: {poll_response.text[:300]}"
                )

            poll_body = poll_response.json()
            task_status = poll_body.get("task_status") or poll_body.get("status", "")

            _log.debug(
                "vlm_task_poll",
                task_id=task_id,
                status=task_status,
                attempt=attempt + 1,
            )

            if task_status in ("success", "SUCCESS"):
                break
            if task_status in ("failure", "FAILURE", "error", "ERROR"):
                meta = poll_body or {}
                errors = (
                    (meta.get("task_meta") or {}).get("errors")
                    or meta.get("errors")
                    or [poll_response.text[:300]]
                )
                raise ConversionError(
                    f"docling VLM: task {task_id} failed: {errors}"
                )
            # Still pending/running — continue polling.
        else:
            raise ConversionError(
                f"docling VLM: task {task_id} did not complete within "
                f"{_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SECONDS} seconds."
            )

        # 3. Fetch and return the result.
        result_response = await self.client.get(
            result_url,
            headers=self._headers(),
            timeout=30,
        )
        if result_response.status_code != 200:
            raise ConversionError(
                f"docling VLM: fetching result for task {task_id} returned HTTP "
                f"{result_response.status_code}: {result_response.text[:300]}"
            )

        _log.info(
            "vlm_task_done",
            converter=self.name,
            filename=filename,
            task_id=task_id,
        )
        return self._extract_markdown(result_response.json(), filename)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _extract_markdown(self, payload: dict[str, Any], filename: str) -> str:
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
