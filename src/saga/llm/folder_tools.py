"""Folder-creation tools for the agentic folder-placement pipeline (FR-16/17).

These tools are passed to ``extract_with_tools`` so the LLM can build the required
folder hierarchy incrementally — one ``create_folder`` call per level — before
submitting its final ``FolderDecision``.  Separating creation from assignment means
the document ends up only in the deepest/most specific folder(s), not in every
ancestor that was created along the way.

Usage::

    tools = build_folder_tools(db)
    result, stats = await extract_with_tools(
        model,
        FolderDecision,
        document_summary,
        tools=tools,
        system_prompt=rendered_folder_agent_prompt,
    )
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from saidex import Tool

if TYPE_CHECKING:
    from saga.storage import PostgresStore

# ---------------------------------------------------------------------------
# Folder-name validation
# ---------------------------------------------------------------------------

_FOLDER_NAME_RE = re.compile(r"^[a-zA-Z0-9_\- ]+$")
_FOLDER_NAME_ALLOWED = "letters (a-z, A-Z), digits (0-9), underscore (_), hyphen (-), and space ( )"
_FOLDER_NAME_CONSTRAINT = (
    "Only letters (a-z, A-Z), digits (0-9), underscore (_), hyphen (-), and space ( ) "
    "are allowed — regex [a-zA-Z0-9_- ]. "
    "Do NOT embed path separators such as '/' in the name; "
    "use separate create_folder calls for each hierarchy level instead."
)


def _validate_folder_name(name: str) -> str | None:
    """Return an error message string if *name* contains invalid characters, else ``None``."""
    if not name or not name.strip():
        return "Folder name must not be empty or blank."
    if not _FOLDER_NAME_RE.match(name):
        bad_chars = sorted({c for c in name if not re.match(r"[a-zA-Z0-9_\- ]", c)})
        return (
            f"Invalid folder name {name!r}: contains forbidden character(s) {bad_chars}. "
            f"Allowed characters: {_FOLDER_NAME_ALLOWED}. "
            "Do NOT use '/' or other path separators; "
            "create one folder per level with separate create_folder calls."
        )
    return None


# ---------------------------------------------------------------------------
# Pydantic parameter schemas for each tool
# ---------------------------------------------------------------------------


class CreateFolderArgs(BaseModel):
    """Arguments for the create_folder tool."""

    name: str = Field(
        description=(
            "Short, human-readable folder name, e.g. 'Invoices' or '2026'. "
            "Must be unique under the given parent. "
            + _FOLDER_NAME_CONSTRAINT
        )
    )
    parent_id: str | None = Field(
        default=None,
        description=(
            "The id of the parent folder (returned by a previous create_folder call "
            "or visible in the folder tree). Null to create a root-level folder."
        ),
    )
    description: str | None = Field(
        default=None,
        description="A short describing what belongs in this folder.",
    )
    emoji: str | None = Field(
        default=None,
        description=(
            "A single emoji visually representing the folder content, e.g. '💰' for "
            "Finance, '📋' for Invoices, '📅' for a year. Display-only; not part of "
            "the folder name."
        ),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_folder_tools(db: PostgresStore) -> list[Tool]:
    """Return the list of tools to pass to ``extract_with_tools`` for folder placement.

    The returned tools are bound to *db* via closure, so the LLM agent can create
    folders directly in Postgres during the loop.

    Args:
        db: The active :class:`~saga.storage.PostgresStore` instance.

    Returns:
        A list containing the ``create_folder`` tool (and potentially more in the
        future, e.g. ``list_folders`` for very large archives).
    """
    return [_make_create_folder_tool(db)]


def _make_create_folder_tool(db: PostgresStore) -> Tool:
    async def _create_folder(
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
        emoji: str | None = None,
    ) -> dict[str, Any]:
        """Create a folder in Postgres and return its id + path for use in assignments."""
        from saga.core.errors import ConflictError

        error = _validate_folder_name(name)
        if error:
            return {"error": error, "allowed_characters": _FOLDER_NAME_ALLOWED}

        try:
            folder = await db.create_folder(
                name=name,
                description=description,
                parent_id=parent_id,
                emoji=emoji,
            )
        except ConflictError:
            # Folder already exists under that parent — find and return it instead.
            existing = await db.list_folders()
            for f in existing:
                if f.name == name and f.parent_id == parent_id:
                    return {
                        "folder_id": f.folder_id,
                        "name": f.name,
                        "already_existed": True,
                    }
            raise

        return {
            "folder_id": folder.folder_id,
            "name": folder.name,
            "already_existed": False,
        }

    return Tool(
        name="create_folder",
        description=(
            "Create a new folder in the archive and return its folder_id. "
            "Use the returned folder_id as parent_id when creating child folders. "
            "If the folder already exists under the same parent, the existing id is "
            "returned (no duplicate is created). "
            "Build the hierarchy top-down: create the root-level folder first, then "
            "its children, down to the most specific leaf folder. "
            "IMPORTANT — folder names may only contain letters (a-z, A-Z), digits (0-9), "
            "underscore (_), hyphen (-), and space ( ). "
            "Never embed path separators such as '/' in the name; "
            "use one create_folder call per hierarchy level instead."
        ),
        parameters=CreateFolderArgs,
        handler=_create_folder,
    )
