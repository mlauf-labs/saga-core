from __future__ import annotations

import pytest

from saga.core.config import load_config


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", "tok-test")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


def test_export_config_default_public_base_url_is_none() -> None:
    cfg = load_config()
    assert cfg.export.public_base_url is None
