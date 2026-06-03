# Backup & restore

DocStore can export every stored document to a local directory tree (FR-28..31).

## Run a backup

```bash
docstore-backup \
  --base-url http://localhost:8000 \
  --token "$DOCSTORE_API_TOKEN" \
  --out ./backup \
  --page-size 50
```

The script pages through `GET /export/documents` (cursor pagination) and, for each
document, downloads the original binary via `GET /documents/{id}/file`.

## Directory layout

For each document, files are written into a directory derived from the **first entry**
of the document's `folder_structure` (FR-30). Each path component is sanitised for the
local filesystem; documents without a folder structure go under `_uncategorized`.

```
backup/
└── Insurance/
    └── Health/
        ├── Policy 2026__<id>.pdf            # original binary (FR-31)
        ├── Policy 2026__<id>.md             # converted Markdown text (FR-31)
        └── Policy 2026__<id>.metadata.json  # all metadata except the Markdown (FR-31)
```

The base filename is `<sanitized-title>__<document_id>` to avoid collisions. The
metadata sidecar contains the full document record (type, extracted values,
`folder_structure`, `category_paths`, hashes, timestamps, …) except `content_markdown`,
which is stored as the `.md` file.

## Notes

- The export endpoint and binary download both require the Bearer token.
- Backups are read-only; they do not modify stored documents.
- The cursor is an opaque token — always pass the `next_cursor` from the previous page
  rather than constructing it yourself.
