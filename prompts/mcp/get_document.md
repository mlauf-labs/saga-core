---
tool: get_document
---

Fetch a single document by `document_id`. Returns the full record: `title`, `summary`,
`doc_type`, `extracted_values`, `folders` (membership with the primary marked),
`notes`, status and timestamps. Set `include_content=false` to omit the converted
Markdown text. Returns `null` if no document with that id exists.
