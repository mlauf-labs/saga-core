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

- `query` (string, required; also matches document titles)
- `top_k` (int, optional; bounded by `mcp.max_top_k`)
- `doc_type` (string, optional)
- `category_path` (string, optional; restricts to a subtree)
- `title` (string, optional; exact-title filter)
- `filters` (object, optional; matches extracted values, e.g. `{ "invoice_number": "12" }`)

Returns a ranked list of snippets, each with `document_id`, `chunk_id`, `snippet`,
`score`, `title`, `doc_type`, `category_paths`.

### `search_documents`
Keyword search over **documents** (title, content, type, category, extracted values)
with filters (FR-20). Use to find/browse whole documents; use `hybrid_search` for
passage-level semantic search.

- `query` (string, optional; omit to browse with filters only)
- `page`, `page_size` (int, optional)
- `doc_type`, `category_path`, `title`, `status` (string, optional filters)
- `filters` (object, optional; matches extracted values)

Returns `{ items, page, page_size, total }`.

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

### `update_document_metadata`
Update a document's editable metadata; lets an agent correct classification, values
or category placement (FR-20). Only provided fields change; changes to
`doc_type`/`category_paths`/`extracted_values` propagate to the search index.

- `document_id` (string, required)
- `doc_type` (string, optional)
- `extracted_values` (array, optional; full replacement)
- `folder_structure` (array of strings, optional)
- `category_paths` (array of strings, optional)

Returns the updated document record.

## Performance

The MCP search path is optimised for low latency (FR-24 / NFR-10): warm pooled
OpenSearch/embedding clients, a server-side hybrid pipeline, denormalised filter
fields on chunks, bounded `top_k`, and stateless HTTP so the server scales
horizontally.
