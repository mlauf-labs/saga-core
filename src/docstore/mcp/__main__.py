"""Entry point for the MCP server (``docstore-mcp``)."""

from __future__ import annotations

from docstore.core.config import load_config
from docstore.core.logging import configure_logging, get_logger


def main() -> None:
    load_config()
    configure_logging()
    log = get_logger("docstore.mcp")
    log.info("mcp_server_start_pending", note="MCP server is implemented in Phase 6.")
    raise NotImplementedError("MCP server is implemented in Phase 6.")


if __name__ == "__main__":
    main()
