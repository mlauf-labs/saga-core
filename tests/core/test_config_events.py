from __future__ import annotations

import pytest

from saga.core.config import AppConfig, EventsConfig, load_config


def test_events_config_defaults() -> None:
    cfg = AppConfig()
    assert cfg.events.publish is False
    assert cfg.events.channel == "saga:events"


def test_events_config_model_defaults() -> None:
    ec = EventsConfig()
    assert ec.publish is False
    assert ec.channel == "saga:events"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", "tok-test")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


def test_events_config_loaded_from_yaml() -> None:
    cfg = load_config()
    assert cfg.events.publish is False
    assert cfg.events.channel == "saga:events"


def test_events_config_bool_coercion_false() -> None:
    ec = EventsConfig(publish="false")  # type: ignore[arg-type]
    assert ec.publish is False


def test_events_config_bool_coercion_true() -> None:
    ec = EventsConfig(publish="true")  # type: ignore[arg-type]
    assert ec.publish is True
