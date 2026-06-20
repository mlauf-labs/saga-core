from __future__ import annotations


def test_metrics_config_defaults() -> None:
    from saga.core.config import AppConfig

    cfg = AppConfig()
    assert cfg.metrics.enabled is True
    assert cfg.metrics.worker_port == 9000
    assert cfg.metrics.prices == {}
