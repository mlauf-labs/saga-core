---
tool: update_folder
---

Update a folder. Provide only the fields to change: `name` (rename), `description`,
`parent_id` (move under another folder — must not create a cycle), `metadata`
(replacement map), or `emoji` (single emoji for visual display — not part of the name).
Renames/moves are re-projected to all affected documents. Returns the updated folder.
