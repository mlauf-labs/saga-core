"""Direct unit tests for the relational system of record (PostgresStore)."""

from __future__ import annotations

import pytest

from saga.core.errors import ConflictError, NotFoundError
from saga.storage.postgres import PostgresStore, ancestor_ids, descendant_ids
from tests.conftest import seed_document


def test_ancestor_ids_dedupes_and_includes_self() -> None:
    parents = {"c": "b", "b": "a", "a": None, "x": "a"}
    assert ancestor_ids(["c", "x"], parents) == ["c", "b", "a", "x"]


def test_descendant_ids_returns_subtree() -> None:
    parents = {"a": None, "b": "a", "c": "b", "d": "a", "e": None}
    assert set(descendant_ids("a", parents)) == {"a", "b", "c", "d"}


async def test_folder_path_root_to_leaf(db: PostgresStore) -> None:
    a = await db.create_folder(name="A")
    b = await db.create_folder(name="B", parent_id=a.folder_id)
    c = await db.create_folder(name="C", parent_id=b.folder_id)
    assert await db.folder_path(c.folder_id) == ["A", "B", "C"]


async def test_unique_folder_name_under_parent(db: PostgresStore) -> None:
    parent = await db.create_folder(name="P")
    await db.create_folder(name="dup", parent_id=parent.folder_id)
    with pytest.raises(ConflictError):
        await db.create_folder(name="dup", parent_id=parent.folder_id)


async def test_summary_embedding_roundtrip(db: PostgresStore) -> None:
    doc = await seed_document(db)
    await db.update_summary(doc.document_id, "short summary", embedding=[0.1, 0.2, 0.3])
    assert await db.get_summary_embedding(doc.document_id) == [0.1, 0.2, 0.3]
    refreshed = await db.get_document(doc.document_id)
    assert refreshed is not None
    assert refreshed.summary == "short summary"


async def test_find_by_hash(db: PostgresStore) -> None:
    doc = await seed_document(db, content_hash="abc123")
    found = await db.find_by_hash("abc123")
    assert found is not None
    assert found.document_id == doc.document_id
    assert await db.find_by_hash("missing") is None


async def test_get_document_titles(db: PostgresStore) -> None:
    a = await seed_document(db, title="Invoice A")
    b = await seed_document(db, title="Contract B")

    titles = await db.get_document_titles([a.document_id, b.document_id, "missing"])
    assert titles == {a.document_id: "Invoice A", b.document_id: "Contract B"}

    # Empty input short-circuits to an empty map.
    assert await db.get_document_titles([]) == {}


async def test_membership_primary_switch_and_fallback(db: PostgresStore) -> None:
    doc = await seed_document(db)
    a = await db.create_folder(name="A")
    b = await db.create_folder(name="B")
    await db.add_document_folder(doc.document_id, a.folder_id, primary=True)
    refs = await db.add_document_folder(doc.document_id, b.folder_id, primary=True)
    primary = {r.folder_id: r.is_primary for r in refs}
    assert primary == {a.folder_id: False, b.folder_id: True}

    # Removing the primary promotes the remaining folder.
    remaining = await db.remove_document_folder(doc.document_id, b.folder_id)
    assert remaining[0].folder_id == a.folder_id


async def test_doc_type_duplicate_name_rejected(db: PostgresStore) -> None:
    created = await db.create_doc_type(name="invoice")
    assert created.name == "invoice"
    with pytest.raises(ConflictError):
        await db.create_doc_type(name="invoice")
    by_name = await db.get_doc_type_by_name("invoice")
    assert by_name is not None
    assert by_name.doc_type_id == created.doc_type_id


async def test_delete_doc_type_in_use_raises(db: PostgresStore) -> None:
    doc_type = await db.create_doc_type(name="contract")
    doc = await seed_document(db)
    await db.update_document(doc.document_id, doc_type_id=doc_type.doc_type_id)
    with pytest.raises(ConflictError):
        await db.delete_doc_type(doc_type.doc_type_id)


async def test_scroll_documents_walks_all(db: PostgresStore) -> None:
    ids = {(await seed_document(db, title=f"d{i}.pdf")).document_id for i in range(5)}
    seen: set[str] = set()
    after: str | None = None
    while True:
        page, after = await db.scroll_documents(page_size=2, after_id=after)
        seen.update(d.document_id for d in page)
        if not after:
            break
    assert seen == ids


async def test_list_documents_in_missing_folder_raises(db: PostgresStore) -> None:
    with pytest.raises(NotFoundError):
        await db.list_documents_in_folder("nope", include_subtree=True, page=1, page_size=10)
