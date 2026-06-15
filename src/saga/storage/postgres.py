"""Relational system of record (Postgres) for documents, folders, doc-types, notes
and document<->folder membership.

Postgres owns the canonical data; OpenSearch holds a rebuildable search projection.
The store exposes a single :class:`PostgresStore` facade with typed methods so the
service/pipeline layers do not depend on SQLAlchemy directly. SQLAlchemy's async
engine (asyncpg) is used; for tests an engine can be injected (e.g. aiosqlite).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from saga.core.errors import ConflictError, NotFoundError, StorageError, ValidationError
from saga.core.logging import get_logger
from saga.core.models import (
    DocType,
    Document,
    DocumentStatus,
    ExtractedValue,
    Folder,
    FolderNode,
    FolderRef,
    Note,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.config import PostgresConfig

_log = get_logger("saga.storage.postgres")

# Portable JSON type: JSONB on Postgres, generic JSON elsewhere (tests on sqlite).
_JSON = JSON().with_variant(JSONB(), "postgresql")


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for the relational models."""


class DocTypeRow(Base):
    __tablename__ = "doc_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FolderRow(Base):
    __tablename__ = "folders"
    __table_args__ = (UniqueConstraint("parent_id", "name", name="uq_folder_parent_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji: Mapped[str | None] = mapped_column(String(10), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )
    folder_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FolderNoteRow(Base):
    __tablename__ = "folder_notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    folder_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("folders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    minio_object: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DocumentStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_type_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("doc_types.id", ondelete="SET NULL"), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_embedding: Mapped[list[float] | None] = mapped_column(_JSON, nullable=True)
    extracted_values: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON, default=list, nullable=False
    )
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentNoteRow(Base):
    __tablename__ = "document_notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentFolderRow(Base):
    __tablename__ = "document_folders"

    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    folder_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------- #
# Pure helpers for the in-memory folder hierarchy (folders are few; load all). #
# --------------------------------------------------------------------------- #


def ancestor_ids(folder_ids: Sequence[str], parents: dict[str, str | None]) -> list[str]:
    """Return ``folder_ids`` plus every ancestor id (deduplicated), for projection."""
    out: list[str] = []
    seen: set[str] = set()
    for fid in folder_ids:
        current: str | None = fid
        while current is not None and current not in seen:
            seen.add(current)
            out.append(current)
            current = parents.get(current)
    return out


def descendant_ids(folder_id: str, parents: dict[str, str | None]) -> list[str]:
    """Return ``folder_id`` and all of its descendants (subtree), inclusive."""
    children: dict[str | None, list[str]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    out: list[str] = []
    stack = [folder_id]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(children.get(current, []))
    return out


class PostgresStore:
    """Async facade over the relational system of record."""

    def __init__(self, config: PostgresConfig, engine: AsyncEngine | None = None) -> None:
        self._config = config
        self._engine = engine
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        if engine is not None:
            self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self._config.dsn,
                echo=self._config.echo,
                pool_size=self._config.pool_size,
                max_overflow=self._config.max_overflow,
                pool_pre_ping=True,
            )
            self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
        return self._engine

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            _ = self.engine  # triggers lazy creation
        if self._sessionmaker is None:  # pragma: no cover - invariant after engine init
            raise StorageError("Postgres session factory was not initialised.")
        return self._sessionmaker

    async def bootstrap(self) -> None:
        """Create all tables if they do not yet exist (greenfield)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _log.info("postgres_bootstrap_done")

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    # ----------------------------- doc-types ------------------------------- #

    async def list_doc_types(self) -> list[DocType]:
        async with self._sessions()() as session:
            rows = (await session.execute(select(DocTypeRow).order_by(DocTypeRow.name))).scalars()
            counts = await self._doc_type_counts(session)
            return [_to_doctype(row, counts.get(row.id, 0)) for row in rows]

    async def get_doc_type(self, doc_type_id: str) -> DocType | None:
        async with self._sessions()() as session:
            row = await session.get(DocTypeRow, doc_type_id)
            if row is None:
                return None
            counts = await self._doc_type_counts(session)
            return _to_doctype(row, counts.get(row.id, 0))

    async def get_doc_type_by_name(self, name: str) -> DocType | None:
        async with self._sessions()() as session:
            row = (
                await session.execute(select(DocTypeRow).where(DocTypeRow.name == name))
            ).scalar_one_or_none()
            return _to_doctype(row, 0) if row is not None else None

    async def create_doc_type(
        self, *, name: str, description: str | None = None, emoji: str | None = None
    ) -> DocType:
        name = name.strip()
        if not name:
            raise ValidationError("A doc-type name must not be empty.")
        async with self._sessions()() as session, session.begin():
            existing = (
                await session.execute(select(DocTypeRow).where(DocTypeRow.name == name))
            ).scalar_one_or_none()
            if existing is not None:
                raise ConflictError(f"A doc-type named '{name}' already exists.")
            row = DocTypeRow(id=_new_id(), name=name, description=description, emoji=emoji)
            session.add(row)
            await session.flush()
            return _to_doctype(row, 0)

    async def ensure_doc_type(
        self, *, name: str, description: str | None = None, emoji: str | None = None
    ) -> DocType:
        """Return the doc-type with ``name``, creating it (with description) if new."""
        name = name.strip()
        if not name:
            name = "other"
        async with self._sessions()() as session, session.begin():
            row = (
                await session.execute(select(DocTypeRow).where(DocTypeRow.name == name))
            ).scalar_one_or_none()
            if row is None:
                row = DocTypeRow(id=_new_id(), name=name, description=description, emoji=emoji)
                session.add(row)
                await session.flush()
            elif description and not row.description:
                row.description = description
                row.updated_at = _now()
            return _to_doctype(row, 0)

    async def update_doc_type(
        self,
        doc_type_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        emoji: str | None = None,
    ) -> DocType:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocTypeRow, doc_type_id)
            if row is None:
                raise NotFoundError(f"Doc-type '{doc_type_id}' was not found.")
            if name is not None:
                row.name = name.strip()
            if description is not None:
                row.description = description
            if emoji is not None:
                # Empty string clears the emoji; None leaves it unchanged.
                row.emoji = emoji.strip() or None
            row.updated_at = _now()
            await session.flush()
            counts = await self._doc_type_counts(session)
            return _to_doctype(row, counts.get(row.id, 0))

    async def delete_doc_type(self, doc_type_id: str) -> None:
        """Delete a doc-type only when no document references it (otherwise 409)."""
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocTypeRow, doc_type_id)
            if row is None:
                raise NotFoundError(f"Doc-type '{doc_type_id}' was not found.")
            in_use = (
                await session.execute(
                    select(func.count())
                    .select_from(DocumentRow)
                    .where(DocumentRow.doc_type_id == doc_type_id)
                )
            ).scalar_one()
            if in_use:
                raise ConflictError(
                    f"Doc-type '{row.name}' is still assigned to {in_use} document(s). "
                    f"Reassign them first (see GET /doc-types/{doc_type_id}/documents)."
                )
            await session.delete(row)

    async def count_documents_with_doc_type(self, doc_type_id: str) -> int:
        async with self._sessions()() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(DocumentRow)
                        .where(DocumentRow.doc_type_id == doc_type_id)
                    )
                ).scalar_one()
            )

    async def _doc_type_counts(self, session: AsyncSession) -> dict[str, int]:
        rows = await session.execute(
            select(DocumentRow.doc_type_id, func.count())
            .where(DocumentRow.doc_type_id.is_not(None))
            .group_by(DocumentRow.doc_type_id)
        )
        return {str(dt): int(count) for dt, count in rows.all() if dt is not None}

    # ------------------------------ folders -------------------------------- #

    async def list_folders(self) -> list[Folder]:
        async with self._sessions()() as session:
            rows = (await session.execute(select(FolderRow).order_by(FolderRow.name))).scalars()
            return [_to_folder(row, notes=[]) for row in rows]

    async def _parents_map(self, session: AsyncSession) -> dict[str, str | None]:
        rows = await session.execute(select(FolderRow.id, FolderRow.parent_id))
        return {str(fid): parent for fid, parent in rows.all()}

    async def parents_map(self) -> dict[str, str | None]:
        """Return ``{folder_id: parent_id}`` for the whole hierarchy."""
        async with self._sessions()() as session:
            return await self._parents_map(session)

    async def get_folder(self, folder_id: str) -> Folder | None:
        async with self._sessions()() as session:
            row = await session.get(FolderRow, folder_id)
            if row is None:
                return None
            notes = await self._folder_notes(session, folder_id)
            return _to_folder(row, notes=notes)

    async def folder_path(self, folder_id: str) -> list[str]:
        """Return folder names from the root down to ``folder_id`` (backup layout)."""
        async with self._sessions()() as session:
            rows = await session.execute(select(FolderRow.id, FolderRow.name, FolderRow.parent_id))
            info = {str(fid): (name, parent) for fid, name, parent in rows.all()}
        names: list[str] = []
        current: str | None = folder_id
        seen: set[str] = set()
        while current is not None and current in info and current not in seen:
            seen.add(current)
            name, parent = info[current]
            names.append(name)
            current = parent
        names.reverse()
        return names

    async def create_folder(
        self,
        *,
        name: str,
        description: str | None = None,
        parent_id: str | None = None,
        metadata: dict[str, str] | None = None,
        emoji: str | None = None,
    ) -> Folder:
        name = name.strip()
        if not name:
            raise ValidationError("A folder name must not be empty.")
        async with self._sessions()() as session, session.begin():
            if parent_id is not None and await session.get(FolderRow, parent_id) is None:
                raise NotFoundError(f"Parent folder '{parent_id}' was not found.")
            clash = (
                await session.execute(
                    select(FolderRow).where(
                        FolderRow.parent_id == parent_id, FolderRow.name == name
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError(
                    f"A folder named '{name}' already exists under the same parent."
                )
            row = FolderRow(
                id=_new_id(),
                name=name,
                description=description,
                parent_id=parent_id,
                folder_metadata=dict(metadata or {}),
                emoji=emoji,
            )
            session.add(row)
            await session.flush()
            return _to_folder(row, notes=[])

    async def update_folder(
        self,
        folder_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        parent_id: str | None = None,
        clear_parent: bool = False,
        metadata: dict[str, str] | None = None,
        emoji: str | None = None,
    ) -> Folder:
        async with self._sessions()() as session, session.begin():
            row = await session.get(FolderRow, folder_id)
            if row is None:
                raise NotFoundError(f"Folder '{folder_id}' was not found.")
            if clear_parent:
                new_parent: str | None = None
            elif parent_id is not None:
                new_parent = parent_id
            else:
                new_parent = row.parent_id
            if new_parent is not None:
                if new_parent == folder_id:
                    raise ValidationError("A folder cannot be its own parent.")
                parents = await self._parents_map(session)
                if folder_id in ancestor_ids([new_parent], parents):
                    raise ValidationError("Cannot move a folder into one of its own descendants.")
                if await session.get(FolderRow, new_parent) is None:
                    raise NotFoundError(f"Parent folder '{new_parent}' was not found.")
            if name is not None:
                row.name = name.strip()
            if description is not None:
                row.description = description
            if metadata is not None:
                row.folder_metadata = dict(metadata)
            if emoji is not None:
                # Empty string clears the emoji; None leaves it unchanged.
                row.emoji = emoji.strip() or None
            if clear_parent or parent_id is not None:
                row.parent_id = new_parent
            row.updated_at = _now()
            await session.flush()
            notes = await self._folder_notes(session, folder_id)
            return _to_folder(row, notes=notes)

    async def delete_folder(self, folder_id: str, *, strategy: str = "reject") -> list[str]:
        """Delete a folder per ``strategy`` and return affected document ids.

        - ``reject`` (default): refuse if the folder has children or documents.
        - ``reparent``: move children + memberships up to the folder's parent.
        - ``cascade``: delete the whole subtree; documents keep existing (memberships
          to deleted folders are removed).

        Returns the ids of documents whose membership changed (for re-projection).
        """
        if strategy not in {"reject", "reparent", "cascade"}:
            raise ValidationError(
                f"Unknown folder delete strategy '{strategy}'. Use reject|reparent|cascade."
            )
        async with self._sessions()() as session, session.begin():
            row = await session.get(FolderRow, folder_id)
            if row is None:
                raise NotFoundError(f"Folder '{folder_id}' was not found.")
            parents = await self._parents_map(session)
            child_ids = [c for c, p in parents.items() if p == folder_id]
            member_doc_ids = await self._folder_member_doc_ids(session, [folder_id])
            if strategy == "reject":
                if child_ids or member_doc_ids:
                    raise ConflictError(
                        f"Folder '{row.name}' is not empty "
                        f"({len(child_ids)} subfolder(s), {len(member_doc_ids)} document(s)). "
                        f"Use strategy=reparent or strategy=cascade, or empty it first."
                    )
                await session.delete(row)
                return []
            if strategy == "reparent":
                for child_id in child_ids:
                    child = await session.get(FolderRow, child_id)
                    if child is not None:
                        child.parent_id = row.parent_id
                # Move memberships to the parent (or drop if no parent).
                affected = await self._reassign_memberships(
                    session, from_folder=folder_id, to_folder=row.parent_id
                )
                await session.delete(row)
                return affected
            # cascade
            subtree = descendant_ids(folder_id, parents)
            affected = await self._folder_member_doc_ids(session, subtree)
            await session.execute(
                delete(DocumentFolderRow).where(DocumentFolderRow.folder_id.in_(subtree))
            )
            await session.execute(delete(FolderRow).where(FolderRow.id.in_(subtree)))
            await self._ensure_primary_for(session, affected)
            return affected

    async def folder_tree(
        self, *, prefix: str | None = None, max_depth: int | None = None
    ) -> list[FolderNode]:
        """Build the folder tree with subtree document counts (FR-22)."""
        async with self._sessions()() as session:
            rows = list((await session.execute(select(FolderRow))).scalars())
            counts = await self._direct_folder_counts(session)
        parents = {row.id: row.parent_id for row in rows}
        nodes: dict[str, FolderNode] = {
            row.id: FolderNode(
                folder_id=row.id,
                name=row.name,
                description=row.description,
                emoji=row.emoji,
                parent_id=row.parent_id,
                metadata=dict(row.folder_metadata or {}),
                document_count=0,
            )
            for row in rows
        }
        # Subtree counts: each folder gets its own direct count plus descendants'.
        for fid in nodes:
            for desc in descendant_ids(fid, parents):
                nodes[fid].document_count += counts.get(desc, 0)
        roots: list[FolderNode] = []
        for row in rows:
            node = nodes[row.id]
            if row.parent_id and row.parent_id in nodes:
                nodes[row.parent_id].children.append(node)
            else:
                roots.append(node)
        _sort_nodes(roots)
        if prefix:
            target = nodes.get(prefix)
            roots = [target] if target is not None else []
        if max_depth is not None:
            _prune_depth(roots, max_depth)
        return roots

    async def _direct_folder_counts(self, session: AsyncSession) -> dict[str, int]:
        rows = await session.execute(
            select(DocumentFolderRow.folder_id, func.count()).group_by(DocumentFolderRow.folder_id)
        )
        return {str(fid): int(count) for fid, count in rows.all()}

    # ---------------------------- folder notes ----------------------------- #

    async def _folder_notes(self, session: AsyncSession, folder_id: str) -> list[Note]:
        rows = (
            await session.execute(
                select(FolderNoteRow)
                .where(FolderNoteRow.folder_id == folder_id)
                .order_by(FolderNoteRow.created_at)
            )
        ).scalars()
        return [_to_note(row) for row in rows]

    async def add_folder_note(self, folder_id: str, content: str) -> Note:
        async with self._sessions()() as session, session.begin():
            if await session.get(FolderRow, folder_id) is None:
                raise NotFoundError(f"Folder '{folder_id}' was not found.")
            row = FolderNoteRow(id=_new_id(), folder_id=folder_id, content=content)
            session.add(row)
            await session.flush()
            return _to_note(row)

    async def update_folder_note(self, note_id: str, content: str) -> Note:
        async with self._sessions()() as session, session.begin():
            row = await session.get(FolderNoteRow, note_id)
            if row is None:
                raise NotFoundError(f"Folder note '{note_id}' was not found.")
            row.content = content
            row.updated_at = _now()
            await session.flush()
            return _to_note(row)

    async def delete_folder_note(self, note_id: str) -> None:
        async with self._sessions()() as session, session.begin():
            row = await session.get(FolderNoteRow, note_id)
            if row is None:
                raise NotFoundError(f"Folder note '{note_id}' was not found.")
            await session.delete(row)

    # ------------------------------ documents ------------------------------ #

    async def create_document(self, document: Document) -> Document:
        async with self._sessions()() as session, session.begin():
            row = DocumentRow(
                id=document.document_id,
                title=document.title,
                filename=document.filename,
                mime_type=document.mime_type,
                size_bytes=document.size_bytes,
                content_hash=document.content_hash,
                minio_object=document.minio_object,
                status=document.status.value,
                error=document.error,
                doc_type_id=document.doc_type_id,
                summary=document.summary,
                extracted_values=[v.model_dump(mode="json") for v in document.extracted_values],
                content_markdown=document.content_markdown,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
            session.add(row)
            await session.flush()
        loaded = await self.get_document(document.document_id)
        if loaded is None:  # pragma: no cover - just created
            raise StorageError(f"Document '{document.document_id}' vanished after creation.")
        return loaded

    async def get_document(self, document_id: str) -> Document | None:
        async with self._sessions()() as session:
            row = await session.get(DocumentRow, document_id)
            if row is None:
                return None
            return await self._load_document(session, row)

    async def find_by_hash(self, content_hash: str) -> Document | None:
        async with self._sessions()() as session:
            row = (
                await session.execute(
                    select(DocumentRow).where(DocumentRow.content_hash == content_hash).limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return await self._load_document(session, row)

    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]:
        async with self._sessions()() as session:
            total = int(
                (await session.execute(select(func.count()).select_from(DocumentRow))).scalar_one()
            )
            rows = list(
                (
                    await session.execute(
                        select(DocumentRow)
                        .order_by(DocumentRow.created_at.desc(), DocumentRow.id.desc())
                        .offset(max(page - 1, 0) * page_size)
                        .limit(page_size)
                    )
                ).scalars()
            )
            documents = [await self._load_document(session, row) for row in rows]
            return documents, total

    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = None
    ) -> tuple[list[Document], str | None]:
        """Cursor pagination over all documents (ordered by id) for export (FR-28)."""
        async with self._sessions()() as session:
            stmt = select(DocumentRow).order_by(DocumentRow.id.asc()).limit(page_size)
            if after_id is not None:
                stmt = stmt.where(DocumentRow.id > after_id)
            rows = list((await session.execute(stmt)).scalars())
            documents = [await self._load_document(session, row) for row in rows]
            next_cursor = rows[-1].id if len(rows) == page_size and rows else None
            return documents, next_cursor

    async def update_status(
        self, document_id: str, status: DocumentStatus, error: str | None = None
    ) -> None:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentRow, document_id)
            if row is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            row.status = status.value
            row.error = error
            row.updated_at = _now()

    async def update_content(self, document_id: str, content_markdown: str) -> None:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentRow, document_id)
            if row is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            row.content_markdown = content_markdown
            row.updated_at = _now()

    async def update_summary(
        self,
        document_id: str,
        summary: str,
        *,
        title: str | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        """Persist the LLM-generated summary (and optionally title + embedding)."""
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentRow, document_id)
            if row is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            row.summary = summary
            if title is not None:
                row.title = title
            if embedding is not None:
                row.summary_embedding = embedding
            row.updated_at = _now()

    async def get_summary_embedding(self, document_id: str) -> list[float] | None:
        async with self._sessions()() as session:
            row = await session.get(DocumentRow, document_id)
            return list(row.summary_embedding) if row and row.summary_embedding else None

    async def update_document(
        self,
        document_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        doc_type_id: str | None = None,
        clear_doc_type: bool = False,
        extracted_values: list[ExtractedValue] | None = None,
    ) -> Document:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentRow, document_id)
            if row is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            if title is not None:
                row.title = title
            if summary is not None:
                row.summary = summary
            if clear_doc_type:
                row.doc_type_id = None
            elif doc_type_id is not None:
                if await session.get(DocTypeRow, doc_type_id) is None:
                    raise NotFoundError(f"Doc-type '{doc_type_id}' was not found.")
                row.doc_type_id = doc_type_id
            if extracted_values is not None:
                row.extracted_values = [v.model_dump(mode="json") for v in extracted_values]
            row.updated_at = _now()
            await session.flush()
            return await self._load_document(session, row)

    async def set_document_doc_type(self, document_id: str, doc_type_id: str | None) -> None:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentRow, document_id)
            if row is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            row.doc_type_id = doc_type_id
            row.updated_at = _now()

    async def delete_document(self, document_id: str) -> None:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentRow, document_id)
            if row is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            await session.delete(row)

    async def list_documents_by_doc_type(
        self, doc_type_id: str, *, page: int, page_size: int
    ) -> tuple[list[Document], int]:
        async with self._sessions()() as session:
            base = select(DocumentRow).where(DocumentRow.doc_type_id == doc_type_id)
            total = int(
                (
                    await session.execute(select(func.count()).select_from(base.subquery()))
                ).scalar_one()
            )
            rows = list(
                (
                    await session.execute(
                        base.order_by(DocumentRow.created_at.desc())
                        .offset(max(page - 1, 0) * page_size)
                        .limit(page_size)
                    )
                ).scalars()
            )
            documents = [await self._load_document(session, row) for row in rows]
            return documents, total

    async def list_documents_in_folder(
        self, folder_id: str, *, include_subtree: bool, page: int, page_size: int
    ) -> tuple[list[Document], int]:
        async with self._sessions()() as session:
            if await session.get(FolderRow, folder_id) is None:
                raise NotFoundError(f"Folder '{folder_id}' was not found.")
            if include_subtree:
                parents = await self._parents_map(session)
                folder_ids = descendant_ids(folder_id, parents)
            else:
                folder_ids = [folder_id]
            doc_ids_stmt = (
                select(DocumentFolderRow.document_id)
                .where(DocumentFolderRow.folder_id.in_(folder_ids))
                .distinct()
            )
            total = int(
                (
                    await session.execute(select(func.count()).select_from(doc_ids_stmt.subquery()))
                ).scalar_one()
            )
            rows = list(
                (
                    await session.execute(
                        select(DocumentRow)
                        .where(DocumentRow.id.in_(doc_ids_stmt))
                        .order_by(DocumentRow.created_at.desc())
                        .offset(max(page - 1, 0) * page_size)
                        .limit(page_size)
                    )
                ).scalars()
            )
            documents = [await self._load_document(session, row) for row in rows]
            return documents, total

    # ---------------------------- document notes --------------------------- #

    async def add_document_note(self, document_id: str, content: str) -> Note:
        async with self._sessions()() as session, session.begin():
            if await session.get(DocumentRow, document_id) is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            row = DocumentNoteRow(id=_new_id(), document_id=document_id, content=content)
            session.add(row)
            await session.flush()
            return _to_note(row)

    async def update_document_note(self, note_id: str, content: str) -> Note:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentNoteRow, note_id)
            if row is None:
                raise NotFoundError(f"Document note '{note_id}' was not found.")
            row.content = content
            row.updated_at = _now()
            await session.flush()
            return _to_note(row)

    async def delete_document_note(self, note_id: str) -> None:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentNoteRow, note_id)
            if row is None:
                raise NotFoundError(f"Document note '{note_id}' was not found.")
            await session.delete(row)

    # ----------------------------- memberships ----------------------------- #

    async def get_document_folders(self, document_id: str) -> list[FolderRef]:
        async with self._sessions()() as session:
            return await self._document_folders(session, document_id)

    async def set_document_folders(
        self,
        document_id: str,
        *,
        folder_ids: Sequence[str],
        primary_id: str | None = None,
        assigned_by: str = "user",
    ) -> list[FolderRef]:
        """Replace a document's folder membership set entirely."""
        async with self._sessions()() as session, session.begin():
            if await session.get(DocumentRow, document_id) is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            unique_ids = list(dict.fromkeys(folder_ids))
            for fid in unique_ids:
                if await session.get(FolderRow, fid) is None:
                    raise NotFoundError(f"Folder '{fid}' was not found.")
            await session.execute(
                delete(DocumentFolderRow).where(DocumentFolderRow.document_id == document_id)
            )
            chosen_primary = (
                primary_id if primary_id in unique_ids else (unique_ids[0] if unique_ids else None)
            )
            for fid in unique_ids:
                session.add(
                    DocumentFolderRow(
                        document_id=document_id,
                        folder_id=fid,
                        is_primary=(fid == chosen_primary),
                        assigned_by=assigned_by,
                    )
                )
            await session.flush()
            return await self._document_folders(session, document_id)

    async def add_document_folder(
        self,
        document_id: str,
        folder_id: str,
        *,
        primary: bool = False,
        assigned_by: str = "user",
    ) -> list[FolderRef]:
        async with self._sessions()() as session, session.begin():
            if await session.get(DocumentRow, document_id) is None:
                raise NotFoundError(f"Document '{document_id}' was not found.")
            if await session.get(FolderRow, folder_id) is None:
                raise NotFoundError(f"Folder '{folder_id}' was not found.")
            existing = await session.get(DocumentFolderRow, (document_id, folder_id))
            if existing is None:
                existing = DocumentFolderRow(
                    document_id=document_id,
                    folder_id=folder_id,
                    is_primary=False,
                    assigned_by=assigned_by,
                )
                session.add(existing)
            if primary:
                await self._clear_primary(session, document_id)
                existing.is_primary = True
            await session.flush()
            await self._ensure_primary_for(session, [document_id])
            return await self._document_folders(session, document_id)

    async def remove_document_folder(self, document_id: str, folder_id: str) -> list[FolderRef]:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentFolderRow, (document_id, folder_id))
            if row is None:
                raise NotFoundError(
                    f"Document '{document_id}' is not assigned to folder '{folder_id}'."
                )
            await session.delete(row)
            await session.flush()
            await self._ensure_primary_for(session, [document_id])
            return await self._document_folders(session, document_id)

    async def set_primary_folder(self, document_id: str, folder_id: str) -> list[FolderRef]:
        async with self._sessions()() as session, session.begin():
            row = await session.get(DocumentFolderRow, (document_id, folder_id))
            if row is None:
                raise NotFoundError(
                    f"Document '{document_id}' is not assigned to folder '{folder_id}'."
                )
            await self._clear_primary(session, document_id)
            row.is_primary = True
            await session.flush()
            return await self._document_folders(session, document_id)

    # ----------------------------- internals ------------------------------- #

    async def _document_folders(self, session: AsyncSession, document_id: str) -> list[FolderRef]:
        rows = await session.execute(
            select(DocumentFolderRow, FolderRow.name, FolderRow.emoji)
            .join(FolderRow, FolderRow.id == DocumentFolderRow.folder_id)
            .where(DocumentFolderRow.document_id == document_id)
            .order_by(FolderRow.name)
        )
        return [
            FolderRef(folder_id=link.folder_id, name=name, emoji=emoji, is_primary=link.is_primary)
            for link, name, emoji in rows.all()
        ]

    async def _clear_primary(self, session: AsyncSession, document_id: str) -> None:
        links = (
            await session.execute(
                select(DocumentFolderRow).where(DocumentFolderRow.document_id == document_id)
            )
        ).scalars()
        for link in links:
            link.is_primary = False

    async def _ensure_primary_for(self, session: AsyncSession, document_ids: Sequence[str]) -> None:
        """Make sure each document with memberships has exactly one primary folder."""
        for document_id in document_ids:
            links = list(
                (
                    await session.execute(
                        select(DocumentFolderRow)
                        .where(DocumentFolderRow.document_id == document_id)
                        .order_by(DocumentFolderRow.created_at)
                    )
                ).scalars()
            )
            if not links:
                continue
            if not any(link.is_primary for link in links):
                links[0].is_primary = True

    async def _reassign_memberships(
        self, session: AsyncSession, *, from_folder: str, to_folder: str | None
    ) -> list[str]:
        links = list(
            (
                await session.execute(
                    select(DocumentFolderRow).where(DocumentFolderRow.folder_id == from_folder)
                )
            ).scalars()
        )
        affected: list[str] = []
        for link in links:
            affected.append(link.document_id)
            if to_folder is None:
                await session.delete(link)
                continue
            existing = await session.get(DocumentFolderRow, (link.document_id, to_folder))
            if existing is None:
                session.add(
                    DocumentFolderRow(
                        document_id=link.document_id,
                        folder_id=to_folder,
                        is_primary=link.is_primary,
                        assigned_by=link.assigned_by,
                    )
                )
            await session.delete(link)
        await session.flush()
        await self._ensure_primary_for(session, affected)
        return affected

    async def _folder_member_doc_ids(
        self, session: AsyncSession, folder_ids: Sequence[str]
    ) -> list[str]:
        rows = await session.execute(
            select(DocumentFolderRow.document_id)
            .where(DocumentFolderRow.folder_id.in_(folder_ids))
            .distinct()
        )
        return [str(doc_id) for (doc_id,) in rows.all()]

    async def _load_document(self, session: AsyncSession, row: DocumentRow) -> Document:
        folders = await self._document_folders(session, row.id)
        note_rows = (
            await session.execute(
                select(DocumentNoteRow)
                .where(DocumentNoteRow.document_id == row.id)
                .order_by(DocumentNoteRow.created_at)
            )
        ).scalars()
        notes = [_to_note(n) for n in note_rows]
        doc_type_name: str | None = None
        if row.doc_type_id is not None:
            dt = await session.get(DocTypeRow, row.doc_type_id)
            doc_type_name = dt.name if dt is not None else None
        return Document(
            document_id=row.id,
            title=row.title,
            filename=row.filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            content_hash=row.content_hash,
            minio_object=row.minio_object,
            status=DocumentStatus(row.status),
            error=row.error,
            content_markdown=row.content_markdown,
            doc_type=doc_type_name,
            doc_type_id=row.doc_type_id,
            summary=row.summary,
            extracted_values=[ExtractedValue.model_validate(v) for v in row.extracted_values],
            folders=folders,
            notes=notes,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )


def _aware(value: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (sqlite drops tz info)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_note(row: DocumentNoteRow | FolderNoteRow) -> Note:
    return Note(
        note_id=row.id,
        content=row.content,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_doctype(row: DocTypeRow, count: int) -> DocType:
    return DocType(
        doc_type_id=row.id,
        name=row.name,
        description=row.description,
        emoji=row.emoji,
        document_count=count,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_folder(row: FolderRow, *, notes: list[Note]) -> Folder:
    return Folder(
        folder_id=row.id,
        name=row.name,
        description=row.description,
        emoji=row.emoji,
        parent_id=row.parent_id,
        metadata=dict(row.folder_metadata or {}),
        notes=notes,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _sort_nodes(nodes: list[FolderNode]) -> None:
    nodes.sort(key=lambda node: node.name.lower())
    for node in nodes:
        _sort_nodes(node.children)


def _prune_depth(nodes: list[FolderNode], remaining: int) -> None:
    for node in nodes:
        if remaining <= 1:
            node.children = []
        else:
            _prune_depth(node.children, remaining - 1)
