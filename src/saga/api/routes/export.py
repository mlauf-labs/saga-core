"""Backup/export endpoint: stream all documents via cursor pagination (FR-28)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import DocumentResponse, ExportPageResponse

router = APIRouter(prefix="/export", tags=["export"], dependencies=[AuthDep])


@router.get(
    "/documents",
    response_model=ExportPageResponse,
    summary="Export all documents (cursor-paginated, includes content + metadata)",
)
async def export_documents(
    services: ServicesDep,
    cursor: Annotated[
        str | None, Query(description="Opaque cursor (document id) from a prior page.")
    ] = None,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> ExportPageResponse:
    pagination = services.config.api.pagination
    effective_size = min(page_size or pagination.default_page_size, pagination.max_page_size)
    documents, next_cursor = await services.db.scroll_documents(
        page_size=effective_size, after_id=cursor
    )
    items: list[DocumentResponse] = []
    for doc in documents:
        response = DocumentResponse.from_document(doc, include_content=True)
        if doc.primary_folder_id is not None:
            response.primary_folder_path = await services.db.folder_path(doc.primary_folder_id)
        items.append(response)
    return ExportPageResponse(items=items, next_cursor=next_cursor)
