"""Behavioural test for the get_store_guidance MCP tool (FR-23).

Follows the harness in test_mcp_event_tools.py: builds the server via
``build_server(config, services)`` using the real SQLite-backed PostgresStore
provided by the ``services`` fixture from conftest.py.  Guidance values are
set directly on ``services.config.generation`` before the server is built.
"""

from __future__ import annotations

from typing import Any

from saga.api.dependencies import Services
from saga.mcp.server import build_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _structured(result: Any) -> Any:
    """Return the structured payload from a FastMCP call_tool result tuple."""
    assert isinstance(result, tuple), f"expected (content, structured) tuple, got {result!r}"
    return result[1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_store_guidance_returns_all_fields(services: Services) -> None:
    """get_store_guidance returns all six config.generation fields."""
    services.config.generation.description = "A family document archive."
    services.config.generation.prompt_folder = "Group by household member."
    services.config.generation.prompt_doctype = ""
    services.config.generation.prompt_metadata = ""
    services.config.generation.prompt_summary = ""

    mcp = build_server(services.config, services)

    result = _structured(await mcp.call_tool("get_store_guidance", {}))

    assert result == {
        "store_description": "A family document archive.",
        "doctype_instructions": "",
        "metadata_instructions": "",
        "summary_instructions": "",
        "folder_instructions": "Group by household member.",
        "language": services.config.generation.language,  # default "English"
    }


async def test_get_store_guidance_default_language(services: Services) -> None:
    """get_store_guidance returns 'English' as the default language when unconfigured."""
    mcp = build_server(services.config, services)

    result = _structured(await mcp.call_tool("get_store_guidance", {}))

    assert result["language"] == "English"


async def test_get_store_guidance_empty_fields_when_unconfigured(services: Services) -> None:
    """get_store_guidance returns empty strings for unconfigured instruction fields."""
    mcp = build_server(services.config, services)

    result = _structured(await mcp.call_tool("get_store_guidance", {}))

    assert result["store_description"] == ""
    assert result["doctype_instructions"] == ""
    assert result["metadata_instructions"] == ""
    assert result["summary_instructions"] == ""
    assert result["folder_instructions"] == ""
