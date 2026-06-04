---
tool: update_document_metadata
---

Update a document's editable metadata. Use this to correct or refine a document's
classification, extracted values, or category placement. Only the fields you provide
are changed; omitted fields are left untouched. Changes to `doc_type`,
`category_paths` or `extracted_values` are automatically propagated to the search
index so subsequent searches and filters stay consistent.

Parameters:
- `document_id` (string, required): the document to update.
- `doc_type` (string, optional): the document type label (e.g. `invoice`).
- `extracted_values` (array, optional): full replacement list of extracted values,
  each `{ "key", "type", "value", "normalized"?, "confidence"? }`.
- `folder_structure` (array of strings, optional): ordered hierarchical paths; the
  first entry is canonical and drives the backup directory layout.
- `category_paths` (array of strings, optional): hierarchical category paths, e.g.
  `["Insurance/Health"]`.

Returns the updated document record. Fails with a not-found error if the document
does not exist, or a validation error if no fields are provided.
