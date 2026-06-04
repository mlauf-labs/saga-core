---
tool: search_documents
---

Search **documents** (not passages) by keyword over their title, converted text,
document type, category paths and extracted values, with optional filters. Use this
to find or browse whole documents — for example by title or category — rather than
retrieving relevant passages (use `hybrid_search` for passage-level semantic search).

Parameters:
- `query` (string, optional): keywords matched across title (boosted), content,
  doc_type, category paths and extracted values. Omit to browse with filters only.
- `page` / `page_size` (integer, optional): pagination.
- `doc_type` (string, optional): filter by document type (e.g. `invoice`).
- `category_path` (string, optional): filter by a category subtree (e.g. `Finance`).
- `title` (string, optional): filter by exact document title.
- `status` (string, optional): filter by processing status (e.g. `ready`).
- `filters` (object, optional): match extracted values, e.g.
  `{ "invoice_number": "12345" }`.

Returns a paginated list of document references (`document_id`, `title`, `doc_type`,
`category_paths`, `created_at`) plus the total count.
