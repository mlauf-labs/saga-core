"""Entry point for the ARQ worker (``docstore-worker``)."""

from __future__ import annotations

from arq import run_worker

from docstore.core.logging import configure_logging
from docstore.pipeline.worker import configure


def main() -> None:
    configure_logging()
    run_worker(configure())  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
