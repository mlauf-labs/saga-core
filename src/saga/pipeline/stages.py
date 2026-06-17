"""Individual ingestion-pipeline stages (FR-4 ff.).

Stages are small, independently testable units. The worker (``tasks.ingest_document``)
orchestrates them and manages status transitions. The pipeline order is:
convert -> classify doc-type -> extract values -> summarise (+embed) -> compute
similarity -> place in folder(s) -> project + index chunks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from saga.core.errors import NotFoundError
from saga.core.logging import get_logger
from saga.core.models import Chunk, ExtractedValue
from saga.search.similarity import score_candidates, vote_folders
from saga.storage.mappings import build_value_terms
from saga.storage.postgres import ancestor_ids

if TYPE_CHECKING:
    from saga.chunking import MarkdownChunker
    from saga.converters import ConverterRegistry
    from saga.core.config import SimilarityConfig
    from saga.core.models import DocType, Folder, FolderRef, FolderVote, SimilarDocument
    from saga.embeddings import EmbeddingProvider
    from saga.events import EventRecorder
    from saga.llm import DocumentAnalyzer
    from saga.llm.schemas import NewFolder
    from saga.storage import MinioStore, OpenSearchStore, PostgresStore

_log = get_logger("saga.pipeline.stages")


async def convert_to_markdown(
    *,
    document_id: str,
    db: PostgresStore,
    minio: MinioStore,
    converters: ConverterRegistry,
) -> str:
    """Download the original binary, convert it to Markdown, and persist it (FR-4)."""
    document = await db.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Cannot convert document '{document_id}': record not found.")
    data = await minio.get_object(document_id)
    converter = converters.resolve(filename=document.filename, mime_type=document.mime_type)
    _log.info(
        "conversion_start",
        document_id=document_id,
        converter=converter.name,
        mime_type=document.mime_type,
    )
    markdown = await converter.convert(
        data=data, filename=document.filename, mime_type=document.mime_type
    )
    await db.update_content(document_id, markdown)
    _log.info("conversion_done", document_id=document_id, chars=len(markdown))
    return markdown


async def classify_doc_type(
    *,
    document_id: str,
    title: str,
    markdown: str,
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    allow_auto_create: bool,
    trace_callbacks: list[Any] | None = None,
    previous_doc_type: str | None = None,
    events: EventRecorder | None = None,
) -> DocType | None:
    """Assign exactly one doc-type, reusing an existing one or creating a new (FR-14)."""
    existing = await db.list_doc_types()
    assignment = await analyzer.classify_doc_type(
        title=title,
        content=markdown,
        existing_doc_types=existing,
        trace_callbacks=trace_callbacks,
    )
    if assignment is None or not assignment.doc_type.strip():
        return None
    by_name = {dt.name: dt for dt in existing}
    match = by_name.get(assignment.doc_type.strip())
    if match is not None:
        doc_type = match
    elif allow_auto_create:
        doc_type = await db.ensure_doc_type(
            name=assignment.doc_type,
            description=assignment.description,
            emoji=assignment.emoji,
        )
    else:
        # Auto-create disabled: only adopt the type if it already exists.
        return None
    await db.set_document_doc_type(document_id, doc_type.doc_type_id)
    _log.info("doc_type_assigned", document_id=document_id, doc_type=doc_type.name)
    if events is not None and doc_type.name != previous_doc_type:
        await events.record_reclassification(
            document_id=document_id,
            from_doc_type=previous_doc_type,
            to_doc_type=doc_type.name,
        )
    return doc_type


async def extract_values(
    *,
    document_id: str,
    markdown: str,
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    trace_callbacks: list[Any] | None = None,
) -> list[ExtractedValue]:
    """Extract identifiers/numbers and persist them on the document (FR-15)."""
    extraction = await analyzer.extract_values(content=markdown, trace_callbacks=trace_callbacks)
    values = [item.to_model() for item in extraction.values] if extraction else []
    values = [v for v in values if v.key]
    await db.update_document(document_id, extracted_values=values)
    _log.info("values_extracted", document_id=document_id, count=len(values))
    return values


async def summarize(
    *,
    document_id: str,
    filename: str,
    markdown: str,
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    embedder: EmbeddingProvider,
    trace_callbacks: list[Any] | None = None,
) -> tuple[str, str, list[float]]:
    """Generate a title + summary, persist them, and embed the summary (FR-14/16).

    The LLM returns both a human-readable ``title`` and a ``summary``.
    If the LLM fails or the title is empty, ``filename`` is used as the title
    so the document always has a meaningful display name.

    Returns ``(summary, title, embedding_vector)``.
    """
    result = await analyzer.summarize(
        filename=filename, content=markdown, trace_callbacks=trace_callbacks
    )
    summary = result.summary.strip() if result and result.summary.strip() else filename
    title = result.title.strip() if result and result.title.strip() else filename
    vectors = await embedder.embed([summary])
    vector = vectors[0] if vectors else []
    await db.update_summary(document_id, summary, title=title, embedding=vector or None)
    _log.info("summary_done", document_id=document_id, chars=len(summary), title=title)
    return summary, title, vector


async def compute_similarity(
    *,
    document_id: str,
    summary: str,
    summary_vector: list[float],
    doc_type: str | None,
    value_terms: list[str],
    opensearch: OpenSearchStore,
    db: PostgresStore,
    config: SimilarityConfig,
) -> tuple[list[SimilarDocument], list[FolderVote]]:
    """Find similar documents and aggregate folder votes (likely placement) (FR-16)."""
    semantic: list[SimilarDocument] = []
    if summary_vector:
        semantic = await opensearch.similar_by_summary(
            query_vector=summary_vector,
            top_k=config.candidate_pool,
            exclude_document_id=document_id,
        )
    lexical = await opensearch.similar_by_text(
        text=summary, top_k=config.candidate_pool, exclude_document_id=document_id
    )
    similar = score_candidates(
        semantic=semantic,
        lexical=lexical,
        new_doc_type=doc_type,
        new_value_terms=value_terms,
        config=config,
    )
    parents = await db.parents_map()
    votes = vote_folders(similar, parents=parents, config=config)
    _log.info(
        "similarity_done",
        document_id=document_id,
        candidates=len(similar),
        folder_votes=len(votes),
    )
    return similar, votes


async def place_in_folder(
    *,
    document_id: str,
    summary: str,
    doc_type: str | None,
    extracted_values: list[ExtractedValue],
    votes: list[FolderVote],
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    allow_auto_create: bool,
    trace_callbacks: list[Any] | None = None,
    similar: list[SimilarDocument] | None = None,
    events: EventRecorder | None = None,
) -> list[FolderRef]:
    """Place the document into 1..n folders, creating new ones when needed (FR-16/17).

    Uses the agentic tool-loop path: the LLM calls ``create_folder`` to build
    the hierarchy, then submits a ``FolderDecision`` that names only the
    deepest/most specific folder(s) — ancestor folders are created but not
    assigned, preventing the "document visible at every hierarchy level" issue.

    Falls back to the legacy single-shot ``FolderPlacement`` path if the agent
    returns ``None`` (e.g. when the model does not support tool calling).
    """
    folders = await db.list_folders()
    parents = await db.parents_map()
    path_by_id = _folder_paths(folders, parents)
    tree_lines = [
        f"- {f.folder_id} — {path_by_id.get(f.folder_id, f.name)} — "
        f"{f.description or 'no description'}"
        for f in folders
    ]
    name_by_id = {f.folder_id: f.name for f in folders}
    likely = (
        "\n".join(
            f"- {vote.folder_id} — {name_by_id.get(vote.folder_id, '?')} (score {vote.score:.2f})"
            for vote in votes
        )
        or "(no similar documents yet)"
    )
    values_summary = ", ".join(f"{v.key}={v.value}" for v in extracted_values) or "none"
    folder_tree_text = "\n".join(tree_lines) if tree_lines else "(no folders yet)"

    # ── Agentic path (primary) ────────────────────────────────────────────────
    decision = await analyzer.place_in_folder_agentic(
        summary=summary,
        doc_type=doc_type or "unknown",
        extracted_values=values_summary,
        folder_tree=folder_tree_text,
        likely_folders=likely,
        allow_auto_create=allow_auto_create,
        db=db,
        trace_callbacks=trace_callbacks,
    )

    assignments: list[str] = []
    primary_id: str | None = None

    if decision is not None:
        # Reload folders so any just-created ones are included in valid_ids.
        fresh_folders = await db.list_folders()
        valid_ids = {f.folder_id for f in fresh_folders}
        assignments = [fid for fid in decision.assignments if fid in valid_ids]
        if decision.primary in valid_ids:
            primary_id = decision.primary

    # ── Legacy fallback (single-shot) ────────────────────────────────────────
    # Used when the agentic path returns None (tool-calling not supported, or
    # the model failed all retries). This path has the ancestor-assignment bug
    # but is better than leaving the document unplaced.
    if not assignments:
        _log.warning("placement_agentic_failed_using_legacy_fallback", document_id=document_id)
        placement = await analyzer.place_in_folder(
            summary=summary,
            doc_type=doc_type or "unknown",
            extracted_values=values_summary,
            folder_tree=folder_tree_text,
            likely_folders=likely,
            allow_auto_create=allow_auto_create,
            trace_callbacks=trace_callbacks,
        )
        valid_ids_legacy = {f.folder_id for f in folders}
        if placement is not None:
            assignments = [fid for fid in placement.assignments if fid in valid_ids_legacy]
            if allow_auto_create:
                created = await _create_new_folders(placement.new_folders, valid_ids_legacy, db)
                primary_new = (
                    placement.new_folder_primary.strip() if placement.new_folder_primary else None
                )
                for name, fid in created.items():
                    if name == primary_new:
                        assignments.append(fid)
                        primary_id = fid
            if placement.primary in valid_ids_legacy:
                primary_id = placement.primary

    # ── Final fallbacks ───────────────────────────────────────────────────────
    if not assignments and votes:
        assignments = [votes[0].folder_id]
    assignments = list(dict.fromkeys(assignments))
    if primary_id not in assignments:
        primary_id = assignments[0] if assignments else None

    if not assignments:
        _log.warning("placement_empty", document_id=document_id)
        return []

    prior_folder_ids: set[str] = set()
    if events is not None:
        prior_folder_ids = {ref.folder_id for ref in await db.get_document_folders(document_id)}

    refs = await db.set_document_folders(
        document_id,
        folder_ids=assignments,
        primary_id=primary_id,
        assigned_by="llm",
    )
    _log.info(
        "placement_done",
        document_id=document_id,
        folders=len(refs),
        primary=primary_id,
    )
    # Emit only when the folder set actually changed, so an idempotent re-ingest does
    # not append a duplicate placement event (and a later revert is still recorded).
    if events is not None and set(assignments) != prior_folder_ids:
        await events.record_placement(
            document_id=document_id,
            folders=assignments,
            primary=primary_id,
            similar=similar or [],
            votes=votes,
        )
    return refs


async def _create_new_folders(
    new_folders: list[NewFolder],
    valid_ids: set[str],
    db: PostgresStore,
) -> dict[str, str]:
    """Create proposed new folders in topological order; returns ``{name: folder_id}``.

    The LLM may propose a deep hierarchy in a single response, e.g.::

        new_folders = [
            NewFolder(name="Finance", parent_id=None),
            NewFolder(name="Invoices", parent_name="Finance"),
            NewFolder(name="2026",     parent_name="Invoices"),
        ]

    ``parent_name`` references another proposal by its ``name`` field.  We process
    proposals in passes: a folder is created as soon as its parent (existing or newly
    created in a prior pass) is available.  Cycles and unresolvable references are
    logged and skipped.
    """
    created: dict[str, str] = {}  # name → folder_id for every new folder created here
    remaining = [p for p in new_folders if p.name.strip()]

    max_passes = len(remaining) + 1  # upper bound to break genuine cycles
    for _pass in range(max_passes):
        if not remaining:
            break
        progress = False
        next_remaining: list[NewFolder] = []

        for proposal in remaining:
            name = proposal.name.strip()

            # Resolve parent: existing folder > sibling new folder > root
            if proposal.parent_id and proposal.parent_id in valid_ids:
                parent_id: str | None = proposal.parent_id
            elif proposal.parent_name and proposal.parent_name.strip() in created:
                parent_id = created[proposal.parent_name.strip()]
            elif proposal.parent_name and proposal.parent_name.strip() not in created:
                # Parent new-folder not yet created — defer to a later pass.
                next_remaining.append(proposal)
                continue
            else:
                parent_id = None  # root

            try:
                folder = await db.create_folder(
                    name=name,
                    description=proposal.description,
                    parent_id=parent_id,
                    emoji=proposal.emoji,
                )
            except Exception as exc:  # pragma: no cover - defensive against clashes
                _log.warning("new_folder_create_failed", name=name, error=str(exc))
                continue

            created[name] = folder.folder_id
            valid_ids.add(folder.folder_id)
            progress = True

        remaining = next_remaining
        if not progress:
            # No forward progress → cycle or dangling parent_name reference.
            for proposal in remaining:
                _log.warning(
                    "new_folder_unresolvable",
                    name=proposal.name,
                    parent_name=proposal.parent_name,
                    hint="parent_name does not match any folder in new_folders; skipping.",
                )
            break

    return created


def _folder_paths(folders: list[Folder], parents: dict[str, str | None]) -> dict[str, str]:
    """Build ``{folder_id: 'Root/Child/Name'}`` for prompt readability."""
    names: dict[str, str] = {fol.folder_id: fol.name for fol in folders}
    paths: dict[str, str] = {}
    for fid in names:
        chain: list[str] = []
        current: str | None = fid
        while current is not None and current in names:
            chain.append(names[current])
            current = parents.get(current)
        paths[fid] = "/".join(reversed(chain))
    return paths


async def index_chunks(
    *,
    document_id: str,
    summary_vector: list[float],
    db: PostgresStore,
    opensearch: OpenSearchStore,
    chunker: MarkdownChunker,
    embedder: EmbeddingProvider,
) -> int:
    """Chunk + embed the document, then project it and its chunks to OpenSearch (FR-6/7/8)."""
    document = await db.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Cannot index document '{document_id}': record not found.")
    parents = await db.parents_map()
    folder_ancestors = ancestor_ids(document.folder_ids, parents)

    await opensearch.project_document(
        document, folder_ancestor_ids=folder_ancestors, summary_embedding=summary_vector or None
    )

    await opensearch.delete_chunks(document_id)
    texts = chunker.split(document.content_markdown or "")
    if not texts:
        _log.warning("no_chunks", document_id=document_id)
        return 0
    vectors = await embedder.embed(texts)
    value_terms = build_value_terms(document.extracted_values)
    chunks = [
        Chunk(
            chunk_id=f"{document_id}:{ordinal}",
            document_id=document_id,
            ordinal=ordinal,
            snippet=text,
            embedding=vector,
            title=document.title,
            doc_type=document.doc_type,
            folder_ids=document.folder_ids,
            folder_ancestor_ids=folder_ancestors,
            value_terms=value_terms,
            status=document.status,
            created_at=document.created_at,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
        )
        for ordinal, (text, vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    indexed = await opensearch.index_chunks(chunks)
    _log.info("chunks_indexed", document_id=document_id, count=indexed)
    return indexed
