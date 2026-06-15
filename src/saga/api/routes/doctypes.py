"""Doc-type management endpoints (FR-14): full CRUD + document listing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from saga.api import service
from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import (
    DocTypeCreate,
    DocTypeUpdate,
    DocumentListResponse,
    DocumentResponse,
)
from saga.core.models import DocType

router = APIRouter(prefix="/doc-types", tags=["doc-types"], dependencies=[AuthDep])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DocType,
    summary="Create a doc-type",
)
async def create_doc_type(services: ServicesDep, body: DocTypeCreate) -> DocType:
    return await service.create_doc_type(
        services, name=body.name, description=body.description, emoji=body.emoji
    )


@router.get("", response_model=list[DocType], summary="List all doc-types")
async def list_doc_types(services: ServicesDep) -> list[DocType]:
    return await services.db.list_doc_types()


@router.get("/{doc_type_id}", response_model=DocType, summary="Get a doc-type by id")
async def get_doc_type(services: ServicesDep, doc_type_id: str) -> DocType:
    return await service.get_doc_type(services, doc_type_id)


@router.patch("/{doc_type_id}", response_model=DocType, summary="Update a doc-type")
async def update_doc_type(services: ServicesDep, doc_type_id: str, body: DocTypeUpdate) -> DocType:
    return await service.update_doc_type(
        services, doc_type_id, name=body.name, description=body.description, emoji=body.emoji
    )


@router.delete(
    "/{doc_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a doc-type (only when unused, else 409)",
)
async def delete_doc_type(services: ServicesDep, doc_type_id: str) -> None:
    await service.delete_doc_type(services, doc_type_id)


@router.get(
    "/{doc_type_id}/documents",
    response_model=DocumentListResponse,
    summary="List documents of a doc-type (for reassignment before deletion)",
)
async def documents_of_doc_type(
    services: ServicesDep,
    doc_type_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    pagination = services.config.api.pagination
    effective_size = min(page_size or pagination.default_page_size, pagination.max_page_size)
    documents, total = await service.list_documents_by_doc_type(
        services, doc_type_id, page=page, page_size=effective_size
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_document(doc, include_content=False) for doc in documents],
        page=page,
        page_size=effective_size,
        total=total,
    )
