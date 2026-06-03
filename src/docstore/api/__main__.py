"""Entry point for the REST API server (``docstore-api``)."""

from __future__ import annotations

import uvicorn

from docstore.core.config import load_config
from docstore.core.logging import configure_logging


def main() -> None:
    cfg = load_config()
    configure_logging()
    uvicorn.run(
        "docstore.api.app:create_app",
        factory=True,
        host=cfg.api.host,
        port=cfg.api.port,
    )


if __name__ == "__main__":
    main()
