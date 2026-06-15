---
tool: delete_folder
---

Delete a folder. `strategy` controls non-empty folders:
- `reject` (default): fail if the folder has subfolders or documents.
- `reparent`: move children and document memberships up to the folder's parent.
- `cascade`: delete the whole subtree (documents are kept; their memberships to the
  deleted folders are removed).

Physical files are never deleted. Pass `folder_id` and `strategy`.
