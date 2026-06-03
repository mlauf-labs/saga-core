"""Unit tests for environment-variable resolution in config loading."""

from __future__ import annotations

import pytest

from docstore.core.config import _resolve_env
from docstore.core.errors import ConfigError


def test_resolve_env_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSTORE_TEST_VAR", "hello")
    assert _resolve_env("${DOCSTORE_TEST_VAR}") == "hello"


def test_resolve_env_uses_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCSTORE_MISSING", raising=False)
    assert _resolve_env("${DOCSTORE_MISSING:-fallback}") == "fallback"


def test_resolve_env_raises_when_required_and_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCSTORE_REQUIRED", raising=False)
    with pytest.raises(ConfigError):
        _resolve_env("${DOCSTORE_REQUIRED}")


def test_resolve_env_mixed_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "db")
    assert _resolve_env("redis://${HOST}:6379/0") == "redis://db:6379/0"
