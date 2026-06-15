"""Unit tests for full config loading and security token parsing."""

from __future__ import annotations

import pytest

from saga.core.config import SecurityConfig, load_config


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", "tok-a, tok-b")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


def test_load_config_from_repo_files() -> None:
    cfg = load_config("config")
    assert cfg.name == "saga"
    assert cfg.api.port == 8000
    assert cfg.api.enable_swagger is True
    assert cfg.opensearch.document_index == "documents"
    assert cfg.opensearch.chunk_index == "document_chunks"
    assert cfg.chunking.max_tokens > 0


def test_security_tokens_split() -> None:
    sec = SecurityConfig(bearer_tokens="tok-a, tok-b ,")
    assert sec.tokens == ["tok-a", "tok-b"]
