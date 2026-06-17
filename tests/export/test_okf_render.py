from __future__ import annotations

from datetime import UTC, datetime

import yaml

from saga.core.models import (
    Document,
    Event,
    EventCategory,
    EventType,
    ExtractedValue,
    FolderRef,
    Note,
)
from saga.export.okf import render_concept, render_index, render_log, resource_uri


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
        notes=[
            Note(
                note_id="n1",
                content="Check me",
                created_at=datetime(2026, 5, 1, tzinfo=UTC),
                updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            )
        ],
    )
    text = render_concept(doc, store_name="saga", public_base_url=None)
    assert text.startswith("---\n")
    front, _, body = text.partition("\n---\n")
    fm = yaml.safe_load(front[len("---\n") :])
    assert fm["type"] == "document"
    assert fm["title"] == "Rechnung ACME"
    assert fm["resource"] == "saga://saga/documents/d1"
    assert fm["tags"] == ["Finanzen"]
    assert fm["saga_id"] == "d1"
    assert fm["saga_extracted_values"][0]["key"] == "total"
    assert fm["saga_notes"][0]["content"] == "Check me"
    assert "# Body" in body
    assert "## Notes" in body and "Check me" in body


def test_render_index_lists_subfolders_and_documents() -> None:
    text = render_index(
        "Finanzen",
        subfolders=[("2026", "2026/index.md", "Year 2026")],
        documents=[
            ("Rechnung ACME", "Rechnung-ACME__d1.md", "One invoice."),
            ("Notiz", "Notiz__d2.md", None),
        ],
    )
    assert text.startswith("# Finanzen")
    assert "## Subfolders" in text
    assert "* [2026](2026/index.md) — Year 2026" in text
    assert "## Documents" in text
    assert "* [Rechnung ACME](Rechnung-ACME__d1.md) — One invoice." in text
    assert "* [Notiz](Notiz__d2.md)" in text
    assert "Notiz__d2.md) —" not in text  # no description → no em-dash suffix


def _event(
    category: EventCategory, etype: EventType, *, occurred: str, recorded: str, summary: str
) -> Event:
    return Event(
        event_id="e",
        category=category,
        event_type=etype,
        occurred_at=datetime.fromisoformat(occurred),
        recorded_at=datetime.fromisoformat(recorded),
        actor="pipeline",
        summary=summary,
    )


def test_render_log_groups_by_date_newest_first_with_category_tags() -> None:
    events = [
        _event(
            EventCategory.AUDIT,
            EventType.PLACEMENT,
            occurred="2026-06-13T10:00:00+00:00",
            recorded="2026-06-13T10:00:00+00:00",
            summary="Placed in 1 folder.",
        ),
        _event(
            EventCategory.CONTENT,
            EventType.APPOINTMENT,
            occurred="2026-05-01T00:00:00+00:00",
            recorded="2026-06-13T10:00:00+00:00",
            summary="Policy expiry.",
        ),
    ]
    text = render_log("Änderungsverlauf — Finanzen", events)
    assert text.startswith("# Änderungsverlauf — Finanzen")
    assert text.index("## 2026-06-13") < text.index("## 2026-05-01")  # newest first
    assert "* **[audit] placement** — Placed in 1 folder." in text
    assert "* **[content] appointment** — Policy expiry." in text
