---
tool: get_document
---

Retrieve a single document by id, including its converted Markdown text and all
extracted metadata (FR-23).

Parameters:
- `document_id` (string, required).
- `include_content` (boolean, optional, default true): include the full Markdown text.

Returns the document record: `document_id`, `title`, `doc_type`, `extracted_values`,
`folder_structure`, `category_paths`, `status`, timestamps, and (optionally)
`content_markdown`.
