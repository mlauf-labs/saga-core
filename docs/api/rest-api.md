# REST API reference

> Base URL: `http://<host>:8000` · Interactive docs: `/docs` (Swagger, toggleable).

All endpoints except `/health` require a **Bearer token** (FR-35):

```
Authorization: Bearer <token>
```

Tokens are configured via `DOCSTORE_API_TOKENS` (comma-separated). Invalid/missing
tokens return `401` with an `auth_error` body.

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

### `GET /documents/{document_id}`
Full document record. Query `include_content` (bool, default `true`) controls whether
`content_markdown` is included. `404` if unknown.

### `GET /documents/{document_id}/status`
Lightweight status view (FR-12): `{ document_id, status, error }`. `status` is one of
`pending | converting | analyzing | indexing | ready | failed`.

### `PUT /documents/{document_id}`
Replace a document = **delete + re-create** (FR-11). `multipart/form-data` with `file`.
The id is preserved or regenerated per `dedup.document_id_on_update` (`keep` | `new`).
Returns `202 Accepted` like upload.

### `DELETE /documents/{document_id}`
Delete the binary (MinIO), the document record, and **all** its chunks (FR-10/FR-26).
Returns `204 No Content`. `404` if unknown.

## Notes

- Ingestion (conversion → LLM analysis → chunking → embedding → indexing) runs on the
  background worker; poll the status endpoint until `ready` or `failed`.
- Search and category-tree endpoints arrive in Phase 6; backup export in Phase 7.
