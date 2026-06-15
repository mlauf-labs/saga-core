---
tool: create_folder
---

Create a new folder. Pass `name` (unique under its parent), and optionally
`description` (context for organising documents), `parent_id` (null for a root folder),
`metadata` (key/value pairs), and `emoji` (a single emoji for visual display).

The `emoji` is purely illustrative and is **not** part of the folder name. When
presenting the created folder, display the emoji before the name (e.g. `💰 Finance`).
Returns the created folder.
