---
tool: get_folder
---

Fetch a single folder by `folder_id`. Returns its `name`, `description`, `parent_id`,
`metadata`, `notes` and timestamps. Returns `null` if no folder with that id exists.

The `emoji` field is a **visual illustration only** — it is NOT part of the folder name.
Display it before the name when presenting the folder (e.g. `📋 Invoices`).
