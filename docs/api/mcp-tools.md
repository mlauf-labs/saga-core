# MCP tools reference

The MCP server runs as its own container over the **Streamable HTTP** transport and
is secured by a **Bearer token** (FR-36). Point your agent's MCP client at:

```
http://<host>:8100/mcp
Authorization: Bearer <token>
```

The token(s) are the same `DOCSTORE_API_TOKENS` used by the REST API. Requests without
a valid token receive `401` with `{ "code": "auth_error", "message": ... }`.

Tool descriptions are authored in [`prompts/mcp/*.md`](../../prompts/mcp) and loaded at
startup, so they can be tuned without code changes (NFR-30).

## Tools

### `hybrid_search`
Hybrid keyword + semantic retrieval over all documents (FR-19).

- `query` (string, required)
- `top_k` (int, optional; bounded by `mcp.max_top_k`)
- `doc_type` (string, optional)
- `category_path` (string, optional; restricts to a subtree)
- `filters` (object, optional; matches extracted values, e.g. `{ "invoice_number": "12" }`)

Returns a ranked list of snippets, each with `document_id`, `chunk_id`, `snippet`,
`score`, `title`, `doc_type`, `category_paths`.

### `get_category_tree`
Return the hierarchical category tree with document counts (FR-22).

- `prefix` (string, optional; subtree root)
- `max_depth` (int, optional)

### `list_documents_in_category`
List documents in a category branch (FR-22).

- `category_path` (string, required)
- `include_subtree` (bool, default `true`)
- `page`, `page_size` (int, optional)

### `get_document`
Fetch a single document by id, including its Markdown and metadata (FR-23).

- `document_id` (string, required)
- `include_content` (bool, default `true`)

## Performance

The MCP search path is optimised for low latency (FR-24 / NFR-10): warm pooled
OpenSearch/embedding clients, a server-side hybrid pipeline, denormalised filter
fields on chunks, bounded `top_k`, and stateless HTTP so the server scales
horizontally.
