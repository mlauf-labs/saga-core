---
tool: get_folder_tree
---

Return the hierarchical folder tree. Each node has `folder_id`, `name`, `description`,
`parent_id`, `metadata`, a subtree `document_count`, and nested `children`.

The `emoji` field is a **visual illustration only** — it is NOT part of the folder name
and must NOT be used in searches or identifiers. When presenting folders to a user,
display the emoji before the name (e.g. `💰 Finance`). If `emoji` is null, omit it.

Optionally pass `prefix` (a folder id) to return only that subtree, and `max_depth` to
limit depth (root level is depth 1).
