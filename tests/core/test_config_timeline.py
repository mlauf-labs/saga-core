from __future__ import annotations

import pytest

from saga.core.config import load_config


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", "tok-test")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


def test_timeline_config_defaults_present() -> None:
    cfg = load_config()
    assert cfg.timeline.rationale_top_n >= 1
    assert cfg.timeline.default_page_size >= 1
    assert cfg.timeline.max_page_size >= cfg.timeline.default_page_size


def test_timeline_content_min_confidence_default() -> None:
    cfg = load_config()
    assert cfg.timeline.content_min_confidence == 0.5


def test_timeline_config_recurrence_defaults() -> None:
    from saga.core.config import TimelineConfig

    cfg = TimelineConfig()
    assert cfg.recurrence_horizon_days == 366
    assert cfg.max_occurrences_per_rule == 366


def test_timeline_config_overrides() -> None:
    from saga.core.config import TimelineConfig

    cfg = TimelineConfig(recurrence_horizon_days=90, max_occurrences_per_rule=50)
    assert cfg.recurrence_horizon_days == 90
    assert cfg.max_occurrences_per_rule == 50
