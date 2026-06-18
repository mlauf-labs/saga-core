---
tool: search_documents
---

Keyword/filter search over documents (title, summary, content, metadata). Leave
`query` empty to browse using filters only.

Filters: `doc_type`, `folder_id` (with `include_subtree`, default true), `title`,
`status`, `filters` (extracted-value matches, e.g. `{ "iban": "DE.." }`), and
`metadata` (free-form metadata matches, e.g. `{ "project": "Apollo" }`). Paginate
with `page` and `page_size`.

Returns `{ items, page, page_size, total }`; each item has `document_id`, `title`,
`doc_type`, `summary`, `folder_ids` and `created_at`.
