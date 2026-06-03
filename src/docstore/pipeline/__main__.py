"""Entry point for the ARQ worker (``docstore-worker``)."""

from __future__ import annotations

from docstore.core.config import load_config
from docstore.core.logging import configure_logging, get_logger


def main() -> None:
    load_config()
    configure_logging()
    log = get_logger("docstore.pipeline")
    log.info("worker_start_pending", note="Worker tasks are implemented in Phases 3-5.")
    raise NotImplementedError("Worker is implemented in Phases 1-5.")


if __name__ == "__main__":
    main()
