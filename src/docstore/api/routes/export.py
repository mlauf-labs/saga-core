"""Backup/export endpoint: stream all documents via cursor pagination (FR-28)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from docstore.api.cursor import decode_cursor, encode_cursor
from docstore.api.dependencies import AuthDep, ServicesDep
from docstore.api.schemas import DocumentResponse, ExportPageResponse

router = APIRouter(prefix="/export", tags=["export"], dependencies=[AuthDep])


@router.get(
    "/documents",
    response_model=ExportPageResponse,
    summary="Export all documents (cursor-paginated, includes content + metadata)",
)
async def export_documents(
    services: ServicesDep,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a prior page.")] = None,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> ExportPageResponse:
    pagination = services.config.api.pagination
    effective_size = min(page_size or pagination.default_page_size, pagination.max_page_size)
    documents, next_sort = await services.opensearch.scroll_documents(
        page_size=effective_size, search_after=decode_cursor(cursor)
    )
    return ExportPageResponse(
        items=[DocumentResponse.from_document(doc, include_content=True) for doc in documents],
        next_cursor=encode_cursor(next_sort) if next_sort is not None else None,
    )
