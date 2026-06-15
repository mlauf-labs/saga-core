---
tool: remove_document_from_folder
---

Remove a document from a folder. Pass `document_id` and `folder_id`. The document and
its file are not deleted; only the membership is removed. If the removed folder was the
primary, another remaining folder becomes primary. Returns the updated folder set.
