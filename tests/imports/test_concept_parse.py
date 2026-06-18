from __future__ import annotations

from datetime import UTC, datetime

from saga.core.models import Document, Note
from saga.export.okf import render_concept, render_notes_suffix
from saga.imports.okf import split_frontmatter, strip_notes_suffix
from saga.okf_keys import metadata_from_frontmatter


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
        "summary": "One invoice.",
        "content_markdown": "# Heading\n\nBody text with a -- dash.",
        "created_at": now,
        "updated_at": now,
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


def test_render_notes_suffix_empty_and_nonempty() -> None:
    assert render_notes_suffix([]) == ""
    assert render_notes_suffix(["a", "b"]) == "\n## Notes\n\n- a\n- b\n"


def test_concept_content_round_trips_with_notes() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    doc = _doc(
        notes=[
            Note(note_id="n1", content="Check me", created_at=now, updated_at=now),
            Note(note_id="n2", content="And me", created_at=now, updated_at=now),
        ]
    )
    text = render_concept(doc, store_name="saga", public_base_url=None)
    fm, body_section = split_frontmatter(text)
    assert fm["saga_id"] == "d1"
    note_contents = [n["content"] for n in fm["saga_notes"]]
    assert strip_notes_suffix(body_section, note_contents) == doc.content_markdown


def test_concept_content_round_trips_without_notes() -> None:
    doc = _doc()
    text = render_concept(doc, store_name="saga", public_base_url=None)
    fm, body_section = split_frontmatter(text)
    assert fm.get("saga_notes") == []
    assert strip_notes_suffix(body_section, []) == doc.content_markdown


def test_split_frontmatter_no_frontmatter_returns_empty() -> None:
    fm, body = split_frontmatter("just text, no fence")
    assert fm == {}
    assert body == "just text, no fence"


def test_metadata_helper_drops_reserved_and_saga_keys() -> None:
    fm = {"type": "invoice", "title": "X", "saga_id": "d1", "owner": "me", "rank": 2}
    assert metadata_from_frontmatter(fm) == {"owner": "me", "rank": "2"}
