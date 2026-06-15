---
tool: create_doc_type
---

Create a new document type. Pass `name` (e.g. `invoice`), an optional `description`
explaining when a document should get this type, and an optional `emoji` (a single
emoji for visual display — **not** part of the name, e.g. `📄` for invoice).

When presenting the created doc-type, display the emoji before the name (e.g. `📄 invoice`).
A doc-type describes *what a document is*, which is independent of folders (where it is
organised). Returns the created type.
