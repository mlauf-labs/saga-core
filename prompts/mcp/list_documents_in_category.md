---
tool: list_documents_in_category
---

List the documents that live in a given category branch (FR-22).

Parameters:
- `category_path` (string, required): the category path, e.g. `Insurance/Health`.
- `include_subtree` (boolean, optional, default true): include documents in
  descendant categories.
- `page` / `page_size` (integer, optional): pagination.

Returns a paginated list of document references (`document_id`, `title`, `doc_type`,
`category_paths`, `created_at`).
