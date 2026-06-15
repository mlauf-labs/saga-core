---
tool: delete_doc_type
---

Delete a document type. Only allowed when **no document** still uses it; otherwise the
call fails. Reassign affected documents first (see `list_documents_in_folder` /
`search_documents` with a `doc_type` filter). Pass `doc_type_id`.
