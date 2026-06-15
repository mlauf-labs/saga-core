# REST API reference

> Base URL: `http://<host>:8000` · Interactive docs: `/docs` (Swagger, toggleable).

All endpoints except `/health` require a **Bearer token** (FR-35):

```
Authorization: Bearer <token>
```

Tokens are configured via `SAGA_API_TOKENS` (comma-separated). Invalid/missing
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
Keyword document search over **title, summary, content, doc-type and extracted
values**, with filters (FR-20). Use this to find/browse whole documents (e.g. by title
or folder); use `POST /search` for fused hybrid search. JSON body:

```json
{
  "query": "liability premium",
  "page": 1,
  "page_size": 25,
  "doc_type": "invoice",
  "folder_id": "fld_finance",
  "include_subtree": true,
  "title": "Invoice 2026.pdf",
  "status": "ready",
  "filters": { "invoice_number": "INV-1" }
}
```

All fields are optional; an empty `query` browses with filters only. `folder_id`
restricts to a folder and, by default (`include_subtree`), its descendants. Returns a
paginated `DocumentListResponse`.

### `GET /documents/{document_id}`
Full document record. Query `include_content` (bool, default `true`) controls whether
`content_markdown` is included. `404` if unknown.

### `GET /documents/{document_id}/status`
Lightweight status view (FR-12): `{ document_id, status, error }`. `status` is one of
`pending | converting | classifying_type | analyzing | summarizing | classifying |
indexing | ready | failed`.

### `POST /documents/{document_id}/reanalyze`
Re-run the full ingestion pipeline for an existing document (re-convert the stored
binary, regenerate metadata, re-chunk and re-index). The document id and stored binary
are kept; the status is reset to `pending` and the job is re-enqueued. Stale chunks are
cleared before re-indexing. Returns `202 Accepted` with `{ document_id, status, title }`;
`404` if unknown. Poll `GET /documents/{id}/status` until `ready`.

### `PATCH /documents/{document_id}`
Update editable document fields (FR-43). JSON body with any of `title`, `summary`,
`doc_type_id`, `extracted_values`; provided fields replace, omitted fields are
unchanged. Changes propagate to the search projection so filters stay consistent.
Folder membership is managed separately (see the membership endpoints below). Returns
the updated `DocumentResponse`. `400` if no fields are provided; `404` if unknown.

```json
{ "summary": "Annual liability policy renewal.", "doc_type_id": "dt_contract" }
```

### `PUT /documents/{document_id}`
Replace a document = **delete + re-create** (FR-11). `multipart/form-data` with `file`.
The id is preserved or regenerated per `dedup.document_id_on_update` (`keep` | `new`).
Returns `202 Accepted` like upload.

### `DELETE /documents/{document_id}`
Delete the binary (MinIO), the document record, and **all** its chunks (FR-10/FR-26).
Returns `204 No Content`. `404` if unknown.

### `POST /search`
**Fused hybrid search** using Reciprocal Rank Fusion (FR-19/20/21). Provide **at least
one** of `keyword_query` (a `query_string` over the document projection — title,
summary, content, doc-type) and `semantic_query` (natural-language kNN over chunk
vectors); the two rankings are merged into a **single ranked document list**. JSON
body:

```json
{
  "keyword_query": "liability premium",
  "semantic_query": "What is the annual premium for the liability policy?",
  "top_k": 10,
  "doc_type": "insurance_policy",
  "folder_id": "fld_insurance",
  "include_subtree": true,
  "title": "Policy.pdf",
  "status": "ready",
  "created_from": "2026-01-01",
  "created_to": "2026-12-31",
  "filters": { "contract_number": "C-12345" }
}
```

`top_k` is bounded by `mcp.max_top_k`; fusion uses `opensearch.rrf_k` (default 60).
Returns `{ "results": [ ... ] }` where each item has `document_id`, `title`, `score`
(fused), `doc_type`, `summary`, `folder_ids`, and an optional best `snippet`.

### `GET /folders`
Return the hierarchical **folder tree** (FR-22). Query: `prefix` (subtree rooted at a
folder id), `max_depth` (≥1). Returns a list of `FolderNode` (each with `folder_id`,
`name`, `description`, `parent_id`, `metadata`, `document_count` subtree count, and
`children`). `GET /folders/flat` returns all folders as a flat list.

### `POST /folders`
Create a folder (FR-41). Body: `{ name, description?, parent_id?, metadata? }`. Returns
the created `Folder` (`201`).

### `GET /folders/{folder_id}`
Get one folder (incl. its notes). `404` if unknown.

### `PATCH /folders/{folder_id}`
Rename / move / describe / update metadata (FR-41). Body with any of `name`,
`description`, `parent_id`, `metadata`; omitted fields unchanged. Returns the updated
`Folder`.

### `DELETE /folders/{folder_id}`
Delete a folder (FR-41). Query `strategy` = `reject` (default; refuse if it has
children), `reparent` (attach children to the parent), or `cascade` (delete the
subtree). Returns `204`.

### `GET /folders/{folder_id}/documents`
List documents in a folder branch (FR-22). Query: `include_subtree` (default `true`),
`page`, `page_size`. Returns a paginated `DocumentListResponse`.

### Folder notes
- `GET /folders/{folder_id}/notes` — list a folder's notes.
- `POST /folders/{folder_id}/notes` — add a note (`{ content }`, `201`).
- `PATCH /folders/{folder_id}/notes/{note_id}` — update a note (`{ content }`).
- `DELETE /folders/{folder_id}/notes/{note_id}` — delete a note (`204`).

### Doc-types — `/doc-types`
First-class document types (FR-14/42):

- `POST /doc-types` — create (`{ name, description? }`, `201`).
- `GET /doc-types` — list all doc-types.
- `GET /doc-types/{doc_type_id}` — get one.
- `PATCH /doc-types/{doc_type_id}` — update (`{ name?, description? }`).
- `DELETE /doc-types/{doc_type_id}` — delete **only when unused**, else `409`.
- `GET /doc-types/{doc_type_id}/documents` — list documents of a doc-type (paginated;
  useful for reassignment before deletion).

### Document notes — `/documents/{id}/notes`
- `GET /documents/{id}/notes` — list a document's notes.
- `POST /documents/{id}/notes` — add a note (`{ content }`, `201`).
- `PATCH /documents/{id}/notes/{note_id}` — update a note (`{ content }`).
- `DELETE /documents/{id}/notes/{note_id}` — delete a note (`204`).

### Folder membership — `/documents/{id}/folders`
Manage which folders a document belongs to (n:m, FR-16/43):

- `GET /documents/{id}/folders` — list the document's memberships
  (`{ folders: [{ folder_id, name, is_primary }] }`).
- `PUT /documents/{id}/folders` — replace the whole membership set
  (`{ folder_ids: [...], primary_id? }`).
- `POST /documents/{id}/folders/{folder_id}` — add to a folder. Query `primary=true`
  marks it primary.
- `PATCH /documents/{id}/folders/{folder_id}` — mark this folder as the primary.
- `DELETE /documents/{id}/folders/{folder_id}` — remove from a folder.

### `GET /documents/{document_id}/file`
Download or inline-preview the original document binary. Streams the bytes with the
stored `mime_type`. Query `disposition` = `attachment` (default) or `inline` (for
in-browser PDF/image preview) sets the `Content-Disposition` header. `404` if unknown;
`422` for an invalid `disposition` value.

### `GET /export/documents`
Stream **all** documents for backup with cursor pagination (FR-28). Query: `cursor`
(opaque token from the previous page; omit for the first page) and `page_size`.
Returns `{ items, next_cursor }` where `items` are full `DocumentResponse` objects
(including `content_markdown` and a resolved **`primary_folder_path`** — folder names
root → primary — used by the backup directory layout) and `next_cursor` is `null` on
the last page.

```bash
# Walk all pages
cursor=""; while :; do
  page=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "http://localhost:8000/export/documents?page_size=50&cursor=$cursor")
  # ...process page.items...
  cursor=$(echo "$page" | jq -r '.next_cursor // empty'); [ -z "$cursor" ] && break
done
```

Prefer the bundled script for full backups: `saga-backup` (see
[`backup.md`](backup.md)).

## Notes

- Ingestion (convert → classify doc-type → extract → summarise → similarity → place →
  project + index) runs on the background worker; poll the status endpoint until
  `ready` or `failed`.
- Documents are hydrated from **Postgres** (the system of record); search ranks via the
  OpenSearch projection and hydrates the results from Postgres.
- Agents use the equivalent **MCP tools** (see [`mcp-tools.md`](mcp-tools.md)).
