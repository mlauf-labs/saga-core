"""Backup/export script (``docstore-backup``), FR-28..31.

Pages through the REST export endpoint and writes, per document, into a directory
derived from ``folder_structure[0]``: the original binary, the converted ``.md`` text,
and a ``*.metadata.json`` sidecar.

Usage:
    docstore-backup --base-url http://localhost:8000 --token <token> --out ./backup
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from docstore.core.logging import configure_logging, get_logger
from docstore.scripts.layout import (
    backup_basename,
    backup_relative_dir,
    metadata_payload,
    original_filename,
)

_log = get_logger("docstore.backup")


async def _write_document(
    client: httpx.AsyncClient, out_dir: Path, document: dict[str, Any]
) -> None:
    document_id = document["document_id"]
    title = document.get("title", document_id)
    rel_dir = out_dir / Path(*backup_relative_dir(document.get("folder_structure", [])).parts)
    rel_dir.mkdir(parents=True, exist_ok=True)
    base = backup_basename(document_id, title)

    # Markdown text.
    markdown = document.get("content_markdown") or ""
    (rel_dir / f"{base}.md").write_text(markdown, encoding="utf-8")

    # Metadata sidecar.
    (rel_dir / f"{base}.metadata.json").write_text(
        json.dumps(metadata_payload(document), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Original binary.
    response = await client.get(f"/documents/{document_id}/file")
    response.raise_for_status()
    (rel_dir / original_filename(document_id, title)).write_bytes(response.content)
    _log.info("backed_up", document_id=document_id, directory=str(rel_dir))


async def run_backup(*, base_url: str, token: str, out: Path, page_size: int) -> int:
    """Download all documents into ``out``. Returns the number of documents written."""
    out.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}"}
    count = 0
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=120.0) as client:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/export/documents", params=params)
            response.raise_for_status()
            page = response.json()
            for document in page["items"]:
                await _write_document(client, out, document)
                count += 1
            cursor = page.get("next_cursor")
            if not cursor:
                break
    _log.info("backup_complete", documents=count, out=str(out))
    return count


def main() -> None:  # pragma: no cover - thin CLI wrapper around run_backup
    parser = argparse.ArgumentParser(description="Back up all DocStore documents to disk.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="REST API base URL.")
    parser.add_argument("--token", required=True, help="Bearer token for the REST API.")
    parser.add_argument("--out", type=Path, default=Path("backup"), help="Output directory.")
    parser.add_argument("--page-size", type=int, default=50, help="Export page size.")
    args = parser.parse_args()

    configure_logging()
    total = asyncio.run(
        run_backup(base_url=args.base_url, token=args.token, out=args.out, page_size=args.page_size)
    )
    _log.info("done", documents=total)


if __name__ == "__main__":
    main()
