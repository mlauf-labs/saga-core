"""Unit tests for logging configuration."""

from __future__ import annotations

from docstore.core.logging import bind_correlation_id, configure_logging, get_logger


def test_configure_and_get_logger() -> None:
    configure_logging(level="INFO", renderer="json")
    log = get_logger("docstore.test", component="unit")
    bind_correlation_id("corr-123")
    # Should not raise.
    log.info("hello", extra_field="value")


def test_console_renderer() -> None:
    configure_logging(level="DEBUG", renderer="console")
    get_logger("docstore.test").debug("debug-line")
