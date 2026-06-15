---
tool: list_documents_in_folder
---

List documents in a folder branch. Pass `folder_id`; by default documents in
descendant folders are included (`include_subtree=true`). Paginate with `page` and
`page_size`. Returns `{ items, page, page_size, total }`.
