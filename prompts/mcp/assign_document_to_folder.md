---
tool: assign_document_to_folder
---

Add a document to an additional folder (membership is many-to-many; the physical file
is stored only once). Pass `document_id` and `folder_id`; set `primary=true` to make
this the document's canonical folder. Returns the document's updated folder set.
