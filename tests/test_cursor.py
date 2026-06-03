"""Unit tests for the pagination cursor codec (FR-28)."""

from __future__ import annotations

import pytest

from docstore.api.cursor import decode_cursor, encode_cursor
from docstore.core.errors import ValidationError


def test_roundtrip() -> None:
    values = [1717000000000, "d1"]
    assert decode_cursor(encode_cursor(values)) == values


def test_decode_none_and_empty() -> None:
    assert decode_cursor(None) is None
    assert decode_cursor("") is None


def test_decode_invalid_raises() -> None:
    with pytest.raises(ValidationError):
        decode_cursor("!!!not-base64!!!")


def test_decode_non_list_raises() -> None:
    import base64

    token = base64.urlsafe_b64encode(b'{"a": 1}').decode()
    with pytest.raises(ValidationError):
        decode_cursor(token)
