---
tool: update_document_metadata
---

Update a document's editable fields. Only the fields you provide are changed; omitted
fields are left untouched. Changes are re-projected to the search index automatically.

Parameters:
- `document_id` (required): the document to update.
- `title` (optional): new display title.
- `summary` (optional): new short summary.
- `doc_type` (optional): a doc-type **id or name**. An unknown name creates a new
  doc-type. A document always has exactly one doc-type.
- `extracted_values` (optional): full replacement list, each
  `{ "key", "type", "value", "normalized"?, "confidence"? }`.
- `metadata` (optional): full replacement free-form metadata map of string values, e.g.
  `{ "project": "Apollo" }`. Keys must not be reserved/OKF-standard or `saga_`-prefixed.

To change folder membership use the folder-assignment tools, not this tool. Returns the
updated document record.
