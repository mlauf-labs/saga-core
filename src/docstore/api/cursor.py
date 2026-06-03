"""Opaque cursor encoding for ``search_after`` pagination (FR-28)."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from docstore.core.errors import ValidationError


def encode_cursor(sort_values: list[Any]) -> str:
    """Encode a ``search_after`` sort tuple into an opaque base64 token."""
    raw = json.dumps(sort_values, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> list[Any] | None:
    """Decode an opaque cursor token back into a ``search_after`` sort tuple."""
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        values = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(f"Invalid pagination cursor: {exc}") from exc
    if not isinstance(values, list):
        raise ValidationError("Invalid pagination cursor: expected an encoded list.")
    return values
