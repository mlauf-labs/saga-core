"""Unit tests for the backup layout helpers (FR-30/31)."""

from __future__ import annotations

from saga.scripts.layout import (
    backup_basename,
    backup_relative_dir,
    metadata_payload,
    original_filename,
    sanitize_component,
)


def test_sanitize_component() -> None:
    assert sanitize_component("Fin/ance") == "Fin_ance"
    assert sanitize_component('a:b*c?"') == "a_b_c__"
    assert sanitize_component("   ") == "_"


def test_backup_relative_dir_uses_primary_folder_path() -> None:
    path = backup_relative_dir(["Insurance", "Health"])
    assert path.parts == ("Insurance", "Health")


def test_backup_relative_dir_sanitizes_each_segment() -> None:
    path = backup_relative_dir(["Fin/ance", "Q1"])
    assert path.parts == ("Fin_ance", "Q1")


def test_backup_relative_dir_unfiled() -> None:
    assert backup_relative_dir([]).parts == ("_unfiled",)


def test_basename_and_original_filename() -> None:
    assert backup_basename("d1", "Invoice 2026.pdf") == "Invoice 2026__d1"
    assert original_filename("d1", "Invoice.pdf") == "Invoice__d1.pdf"
    assert original_filename("d1", "noext") == "noext__d1"


def test_metadata_payload_excludes_markdown() -> None:
    payload = metadata_payload(
        {"document_id": "d1", "content_markdown": "# big", "doc_type": "invoice"}
    )
    assert "content_markdown" not in payload
    assert payload["doc_type"] == "invoice"
