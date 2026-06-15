"""Entry point for the REST API server (``saga-api``)."""

from __future__ import annotations

import uvicorn

from saga.core.config import load_config
from saga.core.logging import configure_logging


def main() -> None:
    cfg = load_config()
    configure_logging()
    uvicorn.run(
        "saga.api.app:create_app",
        factory=True,
        host=cfg.api.host,
        port=cfg.api.port,
    )


if __name__ == "__main__":
    main()
