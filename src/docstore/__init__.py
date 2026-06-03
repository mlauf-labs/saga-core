"""DocStore: a document store for RAG agents.

Ingest documents in any text-convertible format, convert them to Markdown
(via Docling/Kreuzberg), enrich them with LLM-extracted metadata, index them in
OpenSearch (keyword + vector), and expose hybrid search to agents over MCP.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
