# REST API reference

> Base URL: `http://<host>:8000` · Interactive docs: `/docs` (Swagger, toggleable).

All endpoints except `/health` require a **Bearer token** (FR-35):

```
Authorization: Bearer <token>
```

Tokens are configured via `DOCSTORE_API_TOKENS` (comma-separated). Invalid/missing
tokens return `401` with an `auth_error` body.

## CORS

For browser UIs, CORS is configurable via env: `API_CORS_ALLOW_ORIGINS` (comma-separated
origins, `*` allows all — the default) and `API_CORS_ALLOW_CREDENTIALS` (`true`/`false`).
Restrict origins in production. See [configuration](../configuration.md).

## Error format

Every error returns a JSON envelope (NFR-15):

```json
{ "code": "not_found", "message": "Document 'abc' was not found." }
```

| HTTP | `code` | When |
|------|--------|------|
| 400 | `validation_error` | empty file, file too large |
| 401 | `auth_error` | missing/invalid Bearer token |
| 404 | `not_found` | unknown document id |
| 409 | `conflict` | duplicate upload while dedup policy is `reject` |
| 422 | `conversion_error` | document could not be converted (later phases) |
| 502/503 | `provider_error` / `storage_error` | upstream/provider/storage failure |

## Endpoints

### `GET /health`
Liveness probe. Returns `{ "status": "ok", "version": "..." }`. No auth.

### `POST /documents`
Upload a document for **asynchronous** ingestion (FR-1). `multipart/form-data` with a
single `file` field.

- Validates size (`api.max_upload_bytes`) and non-emptiness.
- Computes a SHA-256 content hash and applies the dedup policy
  (`dedup.on_duplicate`: `reject` | `replace` | `allow`, FR-13).
- Stores the binary in MinIO, creates a `pending` document record, and enqueues the
  ingestion job.

Response `202 Accepted`:

```json
{ "document_id": "…", "status": "pending", "title": "invoice.pdf" }
```

### `GET /documents`
Paginated list (newest first), FR-28 / NFR-13.

Query: `page` (≥1, default 1), `page_size` (default `pagination.default_page_size`,
capped at `pagination.max_page_size`). Returns `{ items, page, page_size, total }`.
List items omit `content_markdown`.

### `POST /documents/search`
Keyword document search over **title, content, type, category and extracted values**,
with filters (FR-20). Use this to find/browse whole documents (e.g. by title or
category); use `POST /search` for passage-level semantic search. JSON body:

```json
{
  "query": "liability premium",
  "page": 1,
  "page_size": 25,
  "doc_type": "invoice",
  "category_path": "Finance",
  "title": "Invoice 2026.pdf",
  "status": "ready",
  "filters": { "invoice_number": "INV-1" }
}
```

All fields are optional; an empty `query` browses with filters only. Returns a
paginated `DocumentListResponse`.

### `GET /documents/{document_id}`
Full document record. Query `include_content` (bool, default `true`) controls whether
`content_markdown` is included. `404` if unknown.

### `GET /documents/{document_id}/status`
Lightweight status view (FR-12): `{ document_id, status, error }`. `status` is one of
`pending | converting | analyzing | indexing | ready | failed`.

### `POST /documents/{document_id}/reanalyze`
Re-run the full ingestion pipeline for an existing document (re-convert the stored
binary, regenerate metadata, re-chunk and re-index). The document id and stored binary
are kept; the status is reset to `pending` and the job is re-enqueued. Stale chunks are
cleared before re-indexing. Returns `202 Accepted` with `{ document_id, status, title }`;
`404` if unknown. Poll `GET /documents/{id}/status` until `ready`.

### `PATCH /documents/{document_id}/metadata`
Update editable metadata (FR-20). JSON body with any of `doc_type`,
`extracted_values`, `folder_structure`, `category_paths`; provided fields replace,
omitted fields are unchanged. Changes to `doc_type`/`category_paths`/`extracted_values`
propagate to the chunk/search index so filters stay consistent. Returns the updated
`DocumentResponse`. `400` if no fields are provided; `404` if unknown.

```json
{ "doc_type": "contract", "category_paths": ["Legal/Contracts"] }
```

### `PUT /documents/{document_id}`
Replace a document = **delete + re-create** (FR-11). `multipart/form-data` with `file`.
The id is preserved or regenerated per `dedup.document_id_on_update` (`keep` | `new`).
Returns `202 Accepted` like upload.

### `DELETE /documents/{document_id}`
Delete the binary (MinIO), the document record, and **all** its chunks (FR-10/FR-26).
Returns `204 No Content`. `404` if unknown.

### `POST /search`
Hybrid (keyword + semantic) search (FR-19/20/21). JSON body:

```json
{
  "query": "annual liability premium",
  "top_k": 10,
  "doc_type": "insurance_policy",
  "category_path": "Insurance/Liability",
  "filters": { "contract_number": "C-12345" }
}
```

`top_k` is bounded by `mcp.max_top_k`. The query also matches document titles, and an
optional `title` field filters to an exact title. Returns `{ query, hits }` where each
hit has `document_id`, `chunk_id`, `snippet`, `score`, `title`, `doc_type`, `category_paths`.

### `GET /categories/tree`
Return the derived hierarchical category tree (FR-22). Query: `prefix` (restrict to a
subtree), `max_depth` (≥1). Returns `{ tree: [CategoryNode...] }` where each node has
`path`, `name`, `document_count` (subtree count), and `children`.

### `GET /categories/{category_path}/documents`
List documents in a category branch (FR-22). The path is the category path, e.g.
`/categories/Insurance/Health/documents`. Query: `include_subtree` (default `true`),
`page`, `page_size`. Returns a paginated `DocumentListResponse`.

### `GET /documents/{document_id}/file`
Download or inline-preview the original document binary. Streams the bytes with the
stored `mime_type`. Query `disposition` = `attachment` (default) or `inline` (for
in-browser PDF/image preview) sets the `Content-Disposition` header. `404` if unknown;
`422` for an invalid `disposition` value.

### `GET /export/documents`
Stream **all** documents for backup with cursor pagination (FR-28). Query: `cursor`
(opaque token from the previous page; omit for the first page) and `page_size`.
Returns `{ items, next_cursor }` where `items` are full `DocumentResponse` objects
(including `content_markdown`) and `next_cursor` is `null` on the last page.

```bash
# Walk all pages
cursor=""; while :; do
  page=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "http://localhost:8000/export/documents?page_size=50&cursor=$cursor")
  # ...process page.items...
  cursor=$(echo "$page" | jq -r '.next_cursor // empty'); [ -z "$cursor" ] && break
done
```

Prefer the bundled script for full backups: `docstore-backup` (see
[`backup.md`](backup.md)).

## Notes

- Ingestion (conversion → LLM analysis → chunking → embedding → indexing) runs on the
  background worker; poll the status endpoint until `ready` or `failed`.
- Agents use the equivalent **MCP tools** (see [`mcp-tools.md`](mcp-tools.md)).
- Backup export arrives in Phase 7.
