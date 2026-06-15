"""Structured, coloured logging setup (NFR-16).

Uses ``structlog`` with a coloured console renderer in development and JSON lines
in production. A correlation/job id can be bound to the context so it appears on
every log line.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def configure_logging(level: str = "INFO", renderer: str = "console") -> None:
    """Configure process-wide structured logging.

    Args:
        level: Root log level (e.g. ``INFO``, ``DEBUG``).
        renderer: ``console`` for coloured human output, ``json`` for prod.
    """
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    final_renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=True)
        if renderer == "console"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, final_renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:  # noqa: ANN401
    """Return a bound logger for ``name`` (use the ``saga.<area>`` namespace)."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name).bind(**initial_values)
    return logger


def bind_correlation_id(correlation_id: str) -> None:
    """Bind a correlation/job id to the current context for all subsequent logs."""
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
