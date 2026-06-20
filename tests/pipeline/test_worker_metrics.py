from __future__ import annotations

from unittest.mock import MagicMock

from saga.pipeline import worker


def test_start_metrics_server_uses_config_port(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_start(port: int, registry: object) -> tuple[object, object]:
        calls["port"] = port
        calls["registry"] = registry
        return (MagicMock(), MagicMock())

    monkeypatch.setattr(worker, "start_http_server", fake_start)
    server = worker.start_metrics_server(port=9123, enabled=True)
    assert calls["port"] == 9123
    assert server is not None


def test_start_metrics_server_disabled(monkeypatch) -> None:
    assert worker.start_metrics_server(port=9123, enabled=False) is None
