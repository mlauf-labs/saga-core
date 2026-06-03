# Prompts & tool descriptions

All LLM prompts and MCP tool descriptions live here as Markdown files (NFR-30).
Code loads and renders these at runtime — **do not** inline prompt text as string
literals in Python. This keeps prompts reviewable and versioned independently of code.

## Conventions

- One concern per file.
- Use `{{ variable }}` placeholders; the prompt loader renders them.
- Document expected **output schema** inside the prompt so the LLM returns valid,
  parseable, typed data (FR-18).
- Keep prompts in English (NFR-5).

## Files

| File | Used by | Purpose |
|------|---------|---------|
| `analysis/classification.md` | Worker (FR-14) | Classify document type. |
| `analysis/value-extraction.md` | Worker (FR-15) | Extract identifiers/numbers. |
| `analysis/categorization.md` | Worker (FR-16/17) | Build hierarchical category paths. |
| `mcp/hybrid_search.md` | MCP server (FR-19) | Tool description. |
| `mcp/get_category_tree.md` | MCP server (FR-22) | Tool description. |
| `mcp/list_documents_in_category.md` | MCP server (FR-22) | Tool description. |
| `mcp/get_document.md` | MCP server (FR-23) | Tool description. |
