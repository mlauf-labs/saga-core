---
tool: hybrid_search
---

Search the document store with a **fused hybrid query**. Provide a `keyword_query`, a
`semantic_query`, or both — at least one is required.

- `keyword_query`: OpenSearch `query_string` over documents (title, summary, content,
  doc_type). Supports `AND`/`OR`/`NOT`, grouping `()`, `field:value`, quoted phrases,
  and `*`/`?` wildcards, e.g. `title:Rechnung AND 2024`.
- `semantic_query`: a plain natural-language question matched by meaning over document
  passages, e.g. `Wie hoch ist die monatliche Miete?`.

The two rankings are merged with Reciprocal Rank Fusion into a **single ranked list**
of documents (no more separate keyword/semantic lists).

Optional filters narrow the search:
- `doc_type`: restrict to a document type, e.g. `invoice`.
- `folder_id`: restrict to a folder; by default the whole subtree is included. Set
  `include_subtree=false` to match only that exact folder.
- `title`, `status`, `created_from`/`created_to` (ISO dates), `top_k`.
- `filters` (object): match extracted values, e.g. `{ "invoice_number": "12345" }`.
- `metadata` (object): match free-form document metadata, e.g. `{ "project": "Apollo" }`.

Returns `{ "results": [...] }`, each item with `document_id`, `title`, `score`,
`doc_type`, `summary`, `folder_ids` and the best matching `snippet`. Use `get_document`
to fetch a full document.
