"""Unit test for the backup runner against a mocked export API (FR-28..31)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from saga.scripts.backup import run_backup

BASE_URL = "http://api:8000"


def _document(doc_id: str) -> dict[str, object]:
    return {
        "document_id": doc_id,
        "title": f"{doc_id}.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 3,
        "content_hash": "h",
        "status": "ready",
        "doc_type": "invoice",
        "extracted_values": [],
        "primary_folder_path": ["Finance", "Invoices"],
        "content_markdown": f"# {doc_id}",
        "created_at": "2026-06-03T10:00:00Z",
        "updated_at": "2026-06-03T10:00:00Z",
    }


@respx.mock
async def test_run_backup_writes_files(tmp_path: Path) -> None:
    # Two export pages, then the binary downloads.
    respx.get(f"{BASE_URL}/export/documents").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_document("d00")], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [_document("d01")], "next_cursor": None}),
        ]
    )
    respx.get(url__regex=rf"{BASE_URL}/documents/.+/file").mock(
        return_value=httpx.Response(200, content=b"PDFDATA")
    )

    count = await run_backup(base_url=BASE_URL, token="t", out=tmp_path, page_size=1)
    assert count == 2

    doc_dir = tmp_path / "Finance" / "Invoices"
    assert (doc_dir / "d00__d00.md").read_text(encoding="utf-8") == "# d00"
    assert (doc_dir / "d00__d00.pdf").read_bytes() == b"PDFDATA"
    metadata = json.loads((doc_dir / "d00__d00.metadata.json").read_text(encoding="utf-8"))
    assert metadata["doc_type"] == "invoice"
    assert "content_markdown" not in metadata
