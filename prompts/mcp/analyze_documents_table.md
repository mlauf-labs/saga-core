---
tool: analyze_documents_table
---

Analyse multiple documents and return the extracted values as a structured table.

For each document the tool first checks whether all requested fields are already
stored in the document's metadata (`extracted_values`).  If all fields are found
the LLM is skipped entirely; otherwise only the missing fields are extracted by
the LLM.  Newly extracted values are persisted back to the document so future
calls can reuse them without another LLM invocation.

Parameters:
- `fields` (required): a JSON object where each key becomes a column name in the
  result table and each value describes what to extract for that column, e.g.
  `{"invoice_number": "The invoice number", "total_amount": "The total amount including VAT"}`.
  Between 1 and 10 entries are accepted.
- `document_ids` (optional): list of document ids to analyse.  May be combined
  with `folders`.
- `folders` (optional): list of folder selections.  Each entry is an object with:
  - `folder_id` (required): the folder id.
  - `recursive` (optional, default `false`): when `true`, documents in descendant
    folders are included.

At least one of `document_ids` or `folders` must be provided.

Returns a dict with:
- `columns`: list of column names (`document_id`, `title`, then the field keys).
- `rows`: list of row objects, one per document, with one entry per column.
- `summary`: object with counts `total`, `analyzed_with_llm`,
  `from_metadata_only`, `skipped` (document had no text content yet).
