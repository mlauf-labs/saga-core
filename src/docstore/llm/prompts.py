"""Prompt loading and rendering (NFR-30).

Prompts live as Markdown files under ``prompts/``. An optional YAML front-matter
block (delimited by ``---``) provides metadata such as ``id`` and ``output_schema``.
The body supports ``{{ variable }}`` placeholders rendered at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from docstore.core.errors import ConfigError

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FRONT_MATTER = re.compile(r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)$", re.DOTALL)


def _parse(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group("meta")) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, match.group("body")


def render_prompt(template: str, **variables: Any) -> str:  # noqa: ANN401
    """Render ``{{ var }}`` placeholders in ``template`` with ``variables``."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in variables:
            raise ConfigError(f"Prompt placeholder '{{{{ {name} }}}}' has no value.")
        return str(variables[name])

    return _PLACEHOLDER.sub(_replace, template)


class PromptLibrary:
    """Loads prompt files from a directory and renders them on demand."""

    def __init__(self, root: Path | str = "prompts") -> None:
        self._root = Path(root)

    def load(self, relative_path: str) -> tuple[dict[str, Any], str]:
        """Return ``(metadata, body)`` for the prompt at ``relative_path``."""
        path = self._root / relative_path
        if not path.is_file():
            raise ConfigError(f"Prompt file not found: {path}")
        return _parse(path.read_text(encoding="utf-8"))

    def render(self, relative_path: str, **variables: Any) -> str:  # noqa: ANN401
        """Load and render a prompt with ``variables``."""
        _, body = self.load(relative_path)
        return render_prompt(body, **variables)
