from __future__ import annotations

from datetime import UTC, datetime

import yaml

from saga.core.models import Document, ExtractedValue, FolderRef, Note
from saga.export.okf import render_concept, resource_uri


def _doc(**kw: object) -> Document:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    base: dict[str, object] = {
        "document_id": "d1",
        "title": "Rechnung ACME",
        "filename": "rechnung.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "content_hash": "h",
        "minio_object": "saga-originals/d1",
        "doc_type": "invoice",
        "summary": "One invoice.",
        "content_markdown": "# Body",
        "created_at": now,
        "updated_at": now,
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


def test_resource_uri_prefers_public_base_url() -> None:
    doc = _doc()
    assert resource_uri(doc, store_name="saga", public_base_url="https://x/") == (
        "https://x/documents/d1/file"
    )
    assert resource_uri(doc, store_name="saga", public_base_url=None) == (
        "saga://saga/documents/d1"
    )


def test_render_concept_frontmatter_body_and_notes() -> None:
    doc = _doc(
        doc_type=None,
        folders=[FolderRef(folder_id="f1", name="Finanzen", is_primary=True)],
        extracted_values=[ExtractedValue(key="total", type="money", value="9.99")],
        notes=[Note(note_id="n1", content="Check me", created_at=datetime(2026, 5, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 5, 1, tzinfo=UTC))],
    )
    text = render_concept(doc, store_name="saga", public_base_url=None)
    assert text.startswith("---\n")
    front, _, body = text.partition("\n---\n")
    fm = yaml.safe_load(front[len("---\n"):])
    assert fm["type"] == "document"
    assert fm["title"] == "Rechnung ACME"
    assert fm["resource"] == "saga://saga/documents/d1"
    assert fm["tags"] == ["Finanzen"]
    assert fm["saga_id"] == "d1"
    assert fm["saga_extracted_values"][0]["key"] == "total"
    assert fm["saga_notes"][0]["content"] == "Check me"
    assert "# Body" in body
    assert "## Notes" in body and "Check me" in body
