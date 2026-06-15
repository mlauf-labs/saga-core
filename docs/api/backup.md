# Backup & restore

Saga can export every stored document to a local directory tree (FR-28..31).

## Run a backup

```bash
saga-backup \
  --base-url http://localhost:8000 \
  --token "$SAGA_API_TOKEN" \
  --out ./backup \
  --page-size 50
```

The script pages through `GET /export/documents` (cursor pagination) and, for each
document, downloads the original binary via `GET /documents/{id}/file`.

## Directory layout

For each document, files are written into a directory derived from the document's
**`primary_folder_path`** — the list of folder names from the root down to the
document's primary folder, supplied by the export endpoint (FR-30). Each path component
is sanitised for the local filesystem; documents that belong to no folder go under
`_unfiled`.

```
backup/
└── Insurance/
    └── Health/
        ├── Policy 2026__<id>.pdf            # original binary (FR-31)
        ├── Policy 2026__<id>.md             # converted Markdown text (FR-31)
        └── Policy 2026__<id>.metadata.json  # all metadata except the Markdown (FR-31)
```

The base filename is `<sanitized-title>__<document_id>` to avoid collisions. The
metadata sidecar contains the full document record (doc-type, summary, extracted
values, folders + `primary_folder_path`, notes, hashes, timestamps, …) except
`content_markdown`, which is stored as the `.md` file.

## Notes

- The export endpoint and binary download both require the Bearer token.
- Backups are read-only; they do not modify stored documents.
- The cursor is an opaque token — always pass the `next_cursor` from the previous page
  rather than constructing it yourself.
