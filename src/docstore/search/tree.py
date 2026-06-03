"""Pure helpers to derive the hierarchical category tree from materialised paths
(FR-22). Kept side-effect free for straightforward unit testing.

``document_count`` is the subtree count: the number of (path, count) contributions
at the node's path or any descendant path. A single document tagged with multiple
paths inside the same subtree contributes once per distinct tagged path.
"""

from __future__ import annotations

from docstore.core.models import CategoryNode


def _all_prefixes(path: str) -> list[str]:
    parts = [part for part in path.split("/") if part]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def build_category_tree(
    terms: list[tuple[str, int]],
    *,
    prefix: str | None = None,
    max_depth: int | None = None,
) -> list[CategoryNode]:
    """Build a nested category tree from ``(path, document_count)`` terms.

    Args:
        terms: exact category paths and their direct document counts.
        prefix: if given, only the subtree rooted at this path is returned.
        max_depth: maximum tree depth to return (root level is depth 1).
    """
    direct: dict[str, int] = {}
    node_paths: set[str] = set()
    for path, count in terms:
        normalised = path.strip("/")
        if not normalised:
            continue
        direct[normalised] = direct.get(normalised, 0) + count
        node_paths.update(_all_prefixes(normalised))

    def subtree_count(node_path: str) -> int:
        return sum(
            count
            for path, count in direct.items()
            if path == node_path or path.startswith(f"{node_path}/")
        )

    nodes: dict[str, CategoryNode] = {
        path: CategoryNode(
            path=path,
            name=path.rsplit("/", 1)[-1],
            document_count=subtree_count(path),
        )
        for path in node_paths
    }

    roots: list[CategoryNode] = []
    for path, node in nodes.items():
        if "/" in path:
            nodes[path.rsplit("/", 1)[0]].children.append(node)
        else:
            roots.append(node)

    _sort_recursive(roots)

    if prefix:
        normalised_prefix = prefix.strip("/")
        target = nodes.get(normalised_prefix)
        roots = [target] if target is not None else []

    if max_depth is not None:
        _prune_depth(roots, max_depth)
    return roots


def _sort_recursive(nodes: list[CategoryNode]) -> None:
    nodes.sort(key=lambda node: node.name.lower())
    for node in nodes:
        _sort_recursive(node.children)


def _prune_depth(nodes: list[CategoryNode], remaining: int) -> None:
    for node in nodes:
        if remaining <= 1:
            node.children = []
        else:
            _prune_depth(node.children, remaining - 1)
