"""Unit tests for the category tree builder (FR-22)."""

from __future__ import annotations

from docstore.search.tree import build_category_tree


def test_builds_nested_tree_with_subtree_counts() -> None:
    terms = [
        ("Finance/Invoices", 3),
        ("Finance/Receipts", 1),
        ("Insurance/Health", 2),
    ]
    tree = build_category_tree(terms)
    by_name = {node.name: node for node in tree}
    assert set(by_name) == {"Finance", "Insurance"}
    assert by_name["Finance"].document_count == 4
    invoices = next(c for c in by_name["Finance"].children if c.name == "Invoices")
    assert invoices.path == "Finance/Invoices"
    assert invoices.document_count == 3


def test_prefix_returns_subtree() -> None:
    terms = [("Finance/Invoices/2026", 1), ("Insurance/Health", 1)]
    tree = build_category_tree(terms, prefix="Finance")
    assert len(tree) == 1
    assert tree[0].path == "Finance"
    assert tree[0].children[0].name == "Invoices"


def test_unknown_prefix_returns_empty() -> None:
    assert build_category_tree([("Finance", 1)], prefix="Nope") == []


def test_max_depth_prunes() -> None:
    terms = [("A/B/C/D", 1)]
    tree = build_category_tree(terms, max_depth=2)
    assert tree[0].name == "A"
    assert tree[0].children[0].name == "B"
    assert tree[0].children[0].children == []


def test_empty_and_blank_terms() -> None:
    assert build_category_tree([]) == []
    assert build_category_tree([("", 5)]) == []
