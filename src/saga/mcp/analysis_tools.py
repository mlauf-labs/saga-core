"""Factory for the ``analyze_documents_table`` MCP tool.

The tool lets an agent extract a caller-defined set of fields from multiple
documents in one call and receive the results as a structured table.  It reuses
any values already stored in a document's ``extracted_values`` metadata (matched
by key) to avoid redundant LLM calls, and persists newly extracted values back
to the document so future calls benefit from the cache.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, Field

from saga.api import service
from saga.api.schemas import DocumentPatch
from saga.core.logging import get_logger
from saga.core.models import ExtractedValue

if TYPE_CHECKING:
    from saga.api.dependencies import Services
    from saga.llm.analyzer import DocumentAnalyzer

_log = get_logger("saga.mcp.analysis_tools")

_PAGE_SIZE = 100
_MAX_FIELDS = 10


class FolderSelection(BaseModel):
    """A folder to include in the document selection, with its own recursive flag."""

    folder_id: str = Field(description="The folder id.")
    recursive: bool = Field(
        default=False,
        description="When true, documents in descendant folders are included.",
    )


def build_analyze_documents_table_tool(
    services: Services,
    analyzer: DocumentAnalyzer,
) -> Any:
    """Return the ``analyze_documents_table`` async tool function.

    Closes over *services* and *analyzer* so the tool can access storage and
    the LLM without any additional wiring.
    """

    async def analyze_documents_table(
        fields: Annotated[
            dict[str, str],
            Field(
                description=(
                    "Key/value pairs where each key becomes a column name in the result "
                    "table and the value describes what to extract for that field.  "
                    "Between 1 and 10 pairs are required."
                ),
            ),
        ],
        document_ids: Annotated[
            list[str] | None,
            Field(description="Document ids to analyse.  May be combined with folders."),
        ] = None,
        folders: Annotated[
            list[FolderSelection] | None,
            Field(
                description=(
                    "Folders whose documents should be analysed.  Each entry carries its "
                    "own `recursive` flag that controls whether sub-folders are included."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Analyse multiple documents and return the extracted values as a table.

        For each document the tool first checks whether all requested fields are
        already present in the document's ``extracted_values`` metadata.  If all
        are found the LLM is skipped entirely; otherwise the LLM extracts only the
        missing fields.  Newly extracted values are merged back into the document's
        metadata so subsequent calls can reuse them.

        Returns a dict with:
        - ``columns``: list of column names (document_id, title, …field keys…).
        - ``rows``: list of dicts, one per document.
        - ``summary``: counts of ``total``, ``analyzed_with_llm``,
          ``from_metadata_only``, ``skipped`` (no content available).
        """
        # ── Input validation ────────────────────────────────────────────────
        if not document_ids and not folders:
            return {
                "error": "At least one of 'document_ids' or 'folders' must be provided."
            }

        if not fields:
            return {"error": "'fields' must contain at least one entry."}

        if len(fields) > _MAX_FIELDS:
            return {
                "error": (
                    f"'fields' accepts at most {_MAX_FIELDS} entries; "
                    f"{len(fields)} were provided."
                )
            }

        # ── Collect documents (stable order, dedupe by document_id) ─────────
        seen: set[str] = set()
        documents = []

        for doc_id in document_ids or []:
            if doc_id in seen:
                continue
            doc = await services.search.get_document(doc_id)
            if doc is None:
                _log.warning("analyze_table_doc_not_found", document_id=doc_id)
                continue
            seen.add(doc_id)
            documents.append(doc)

        for folder_sel in folders or []:
            page = 1
            while True:
                docs, total = await services.search.list_documents_in_folder(
                    folder_id=folder_sel.folder_id,
                    include_subtree=folder_sel.recursive,
                    page=page,
                    page_size=_PAGE_SIZE,
                )
                for doc in docs:
                    if doc.document_id not in seen:
                        seen.add(doc.document_id)
                        documents.append(doc)
                if page * _PAGE_SIZE >= total or not docs:
                    break
                page += 1

        if not documents:
            return {
                "columns": ["document_id", "title", *fields.keys()],
                "rows": [],
                "summary": {
                    "total": 0,
                    "analyzed_with_llm": 0,
                    "from_metadata_only": 0,
                    "skipped": 0,
                },
            }

        # ── Per-document analysis ────────────────────────────────────────────
        field_keys = list(fields.keys())
        columns = ["document_id", "title", *field_keys]
        rows: list[dict[str, Any]] = []
        total_count = len(documents)
        llm_count = 0
        meta_count = 0
        skipped_count = 0

        for doc in documents:
            existing: dict[str, str] = {ev.key: ev.value for ev in doc.extracted_values}
            missing_keys = [k for k in field_keys if k not in existing]

            if not missing_keys:
                meta_count += 1
                row_values = {k: existing.get(k) for k in field_keys}
            elif doc.content_markdown is None:
                skipped_count += 1
                row_values = {k: existing.get(k) for k in field_keys}
                _log.debug(
                    "analyze_table_no_content",
                    document_id=doc.document_id,
                    missing_fields=missing_keys,
                )
            else:
                missing_field_defs = {k: fields[k] for k in missing_keys}
                extracted = await analyzer.extract_fields(
                    content=doc.content_markdown,
                    fields=missing_field_defs,
                )
                llm_count += 1

                new_evs = [
                    ExtractedValue(
                        key=k,
                        type="other",
                        value=v if v is not None else "",
                        normalized=None,
                        confidence=1.0,
                    )
                    for k, v in extracted.items()
                    if v is not None
                ]

                if new_evs:
                    merged = list(doc.extracted_values) + new_evs
                    patch = DocumentPatch(extracted_values=merged)
                    try:
                        await service.update_document(services, doc.document_id, patch)
                        _log.info(
                            "analyze_table_persisted",
                            document_id=doc.document_id,
                            new_fields=len(new_evs),
                        )
                    except Exception as exc:  # noqa: BLE001
                        _log.warning(
                            "analyze_table_persist_failed",
                            document_id=doc.document_id,
                            error=str(exc),
                        )

                row_values = {k: existing.get(k) or extracted.get(k) for k in field_keys}

            rows.append(
                {
                    "document_id": doc.document_id,
                    "title": doc.title,
                    **row_values,
                }
            )

        return {
            "columns": columns,
            "rows": rows,
            "summary": {
                "total": total_count,
                "analyzed_with_llm": llm_count,
                "from_metadata_only": meta_count,
                "skipped": skipped_count,
            },
        }

    return analyze_documents_table
