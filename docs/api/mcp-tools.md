# MCP tools reference

The MCP server runs as its own container over the **Streamable HTTP** transport and
is secured by a **Bearer token** (FR-36). Point your agent's MCP client at:

```
http://<host>:8100/mcp
Authorization: Bearer <token>
```

The token(s) are the same `SAGA_API_TOKENS` used by the REST API. Requests without
a valid token receive `401` with `{ "code": "auth_error", "message": ... }`.

Tool descriptions are authored in [`prompts/mcp/*.md`](../../prompts/mcp) and loaded at
startup, so they can be tuned without code changes (NFR-30).

The server exposes **read** tools (search + browse) and **write** tools (organise the
store). Reads reuse the shared `SearchService`; writes reuse the shared service layer
so agents and the REST API behave identically and keep the projection consistent.

## Read tools

### `hybrid_search`
**Fused hybrid retrieval** (FR-19/20/21). Provide **at least one** of two independent
queries; an error is returned if both are empty:

- `keyword_query` (string, optional): OpenSearch `query_string` over the **document
  projection** (title, summary, content, doc-type; supports `AND`/`OR`/`NOT`,
  grouping, `field:value`, quoted phrases, `*`/`?` wildcards).
- `semantic_query` (string, optional): natural-language kNN query over the **chunk
  index** (passages).

The two rankings are merged into a **single fused document list** via Reciprocal Rank
Fusion (`opensearch.rrf_k`). Filters apply to whichever query runs:

- `top_k` (int, optional; bounded by `mcp.max_top_k`)
- `doc_type` (string, optional)
- `folder_id` (string, optional) + `include_subtree` (bool, default `true`; include
  descendant folders)
- `title` (string, optional; exact-title filter)
- `status` (string, optional; e.g. `ready`)
- `created_from` / `created_to` (ISO date/datetime, optional; inclusive range)
- `filters` (object, optional; matches extracted values, e.g. `{ "invoice_number": "12" }`)

Returns a single list: `{ "results": [ { document_id, title, filename, score, doc_type, summary,
folder_ids, snippet } ] }` — **no** separate keyword/semantic lists.
`filename` is the original upload name; `title` is the LLM-generated display title.

### `search_documents`
Keyword search over **documents** (title, summary, content, doc-type, extracted
values) with filters (FR-20). Use to find/browse whole documents; use `hybrid_search`
for fused retrieval.

- `query` (string, optional; omit to browse with filters only)
- `page`, `page_size` (int, optional)
- `doc_type`, `title`, `status` (string, optional filters)
- `folder_id` (string, optional) + `include_subtree` (bool, default `true`)
- `filters` (object, optional; matches extracted values)

Returns `{ items, page, page_size, total }`.

### `get_document`
Fetch a single document by id, including its Markdown and metadata (FR-23).

- `document_id` (string, required) · `include_content` (bool, default `true`)

Response includes both `title` (LLM-generated, editable) and `filename` (original upload name, read-only).

### `get_folder_tree`
Return the hierarchical **folder tree** with document counts (FR-22).

- `prefix` (string, optional; subtree rooted at a folder id) · `max_depth` (int, optional)

### `get_folder`
Fetch a single folder (incl. its notes) by id. `folder_id` (string, required).

### `list_documents_in_folder`
List documents in a folder branch (FR-22).

- `folder_id` (string, required) · `include_subtree` (bool, default `true`)
- `page`, `page_size` (int, optional)

### `list_doc_types`
List all doc-types (id, name, description, document count).

### `get_timeline`
Query the timeline — **audit** events (what the pipeline did and why) and **content**
events (dated facts) (FR-44…FR-48). Returns `{ items, limit, offset }`.

- `document_id` (string, optional) · `folder_id` (string, optional, subtree)
- `category` (`audit` | `content`; omit for both)
- `order_by` (`recorded_at` default | `occurred_at`) · `limit`, `offset`

### `get_agenda`
The **upcoming** view: future and recurring content events, ascending by date with recurring
rules expanded into occurrences (FR-47/49). Returns `{ items, limit, offset }`.

- `folder_id` (string, optional, subtree) · `limit`, `offset`

## Write tools

These let an agent **reorganise** the store; all writes go through the shared service
layer so the OpenSearch projection stays consistent (FR-23/43).

### `update_document_metadata`
Correct a document's editable fields. Only provided fields change.

- `document_id` (string, required)
- `title`, `summary` (string, optional)
- `doc_type` (string, optional; a doc-type **id or name** — a new name is created)
- `extracted_values` (array, optional; full replacement)

### Folder membership
- `assign_document_to_folder` — `document_id`, `folder_id`, `primary?` (bool).
- `remove_document_from_folder` — `document_id`, `folder_id`.
- `set_document_folders` — `document_id`, `folder_ids` (full set), `primary_id?`.
- `set_primary_folder` — `document_id`, `folder_id`.

Each returns `{ folders: [{ folder_id, name, is_primary }] }`.

### Folder CRUD
- `create_folder` — `name`, `description?`, `parent_id?`, `metadata?`.
- `update_folder` — `folder_id`, then any of `name`, `description`, `parent_id` (move),
  `metadata`.
- `delete_folder` — `folder_id`, `strategy` = `reject` | `reparent` | `cascade`.

### Doc-type CRUD
- `create_doc_type` — `name`, `description?`.
- `update_doc_type` — `doc_type_id`, `name?`, `description?`.
- `delete_doc_type` — `doc_type_id` (must be unused).

### Notes
- Documents: `add_document_note` (`document_id`, `content`),
  `update_document_note` (`note_id`, `content`), `delete_document_note` (`note_id`).
- Folders: `add_folder_note` (`folder_id`, `content`),
  `update_folder_note` (`note_id`, `content`), `delete_folder_note` (`note_id`).

## Performance

The MCP search path is optimised for low latency (FR-24 / NFR-10): warm pooled
OpenSearch/embedding clients, in-app RRF fusion, denormalised filter fields, bounded
`top_k`, and stateless HTTP so the server scales horizontally.
