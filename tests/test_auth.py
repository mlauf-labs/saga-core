"""Unit tests for Bearer-token verification (FR-35)."""

from __future__ import annotations

import pytest

from docstore.api.auth import verify_bearer_token
from docstore.core.errors import AuthError


def test_valid_token_passes() -> None:
    verify_bearer_token("tok-a", ["tok-a", "tok-b"])


def test_missing_token_raises() -> None:
    with pytest.raises(AuthError):
        verify_bearer_token(None, ["tok-a"])


def test_invalid_token_raises() -> None:
    with pytest.raises(AuthError):
        verify_bearer_token("nope", ["tok-a"])
