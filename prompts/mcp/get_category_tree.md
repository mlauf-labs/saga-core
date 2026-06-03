---
tool: get_category_tree
---

Return the **hierarchical category tree** of the document store, with the number of
documents under each node. Use this to understand how documents are organised before
drilling into a specific category.

Parameters:
- `prefix` (string, optional): only return the subtree under this path
  (e.g. `Insurance`).
- `max_depth` (integer, optional): limit the depth of the returned tree.

Returns a nested tree of category nodes with `path`, `name`, `document_count`, and
`children`.
