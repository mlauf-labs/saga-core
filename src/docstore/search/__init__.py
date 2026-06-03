"""Search: hybrid retrieval and category-tree browsing (FR-19/22)."""

from __future__ import annotations

from docstore.search.service import SearchService
from docstore.search.tree import build_category_tree

__all__ = ["SearchService", "build_category_tree"]
