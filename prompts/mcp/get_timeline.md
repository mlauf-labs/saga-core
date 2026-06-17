Return timeline events for the archive: a tagged, chronological log.

There are two kinds of events, selected with `category`:

- `audit` — what happened inside the archive (a document was ingested, classified,
  placed into folders, or moved), including *why* a document was auto-placed (the
  similar documents and folder votes that drove the decision).
- `content` — dates and appointments extracted from document text (e.g. a contract
  start date or a renewal deadline). Sorted by their real-world date.

Filters:
- `document_id` — only events about one document.
- `folder_id` — only events scoped to a folder and its subtree.
- `category` — `audit`, `content`, or omit for both.
- `order_by` — `recorded_at` (when it was archived) or `occurred_at` (the event's own date).
- `limit` / `offset` — pagination.

Use this to answer questions like "what happened to this document?", "why was it filed
here?", or "what is coming up?".
