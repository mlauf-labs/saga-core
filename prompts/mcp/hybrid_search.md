---
tool: hybrid_search
---

Search the document store using **hybrid retrieval** (keyword/BM25 + semantic vector
search combined and re-ranked). Use this to find passages relevant to a question
across all stored documents.

Parameters:
- `query` (string, required): natural-language query or keywords.
- `top_k` (integer, optional): number of snippets to return (bounded by server max).
- `doc_type` (string, optional): restrict to a document type (e.g. `invoice`).
- `category_path` (string, optional): restrict to a category subtree
  (e.g. `Insurance/Health`).
- `filters` (object, optional): match extracted values, e.g.
  `{ "invoice_number": "12345" }`.

Returns a ranked list of snippets. Each result includes the snippet text, a relevance
score, and a reference to the parent document (`document_id`, `title`, `doc_type`,
`category_paths`). Use `get_document` to retrieve the full document.
