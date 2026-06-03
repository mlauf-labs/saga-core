"""Document management endpoints (FR-1, FR-10, FR-11, FR-12, FR-28)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from docstore.api import service
from docstore.api.dependencies import AuthDep, ServicesDep
from docstore.api.schemas import (
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusResponse,
    UploadAcceptedResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[AuthDep])

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadAcceptedResponse,
    summary="Upload a document for asynchronous ingestion",
)
async def upload_document(
    services: ServicesDep,
    file: Annotated[UploadFile, File(description="The document binary to ingest.")],
) -> UploadAcceptedResponse:
    data = await file.read()
    document = await service.create_document(
        services,
        data=data,
        filename=file.filename or "untitled",
        content_type=file.content_type or _DEFAULT_CONTENT_TYPE,
    )
    return UploadAcceptedResponse(
        document_id=document.document_id, status=document.status, title=document.title
    )


@router.get("", response_model=DocumentListResponse, summary="List documents (paginated)")
async def list_documents(
    services: ServicesDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    pagination = services.config.api.pagination
    effective_size = page_size or pagination.default_page_size
    effective_size = min(effective_size, pagination.max_page_size)
    documents, total = await services.opensearch.list_documents(page=page, page_size=effective_size)
    return DocumentListResponse(
        items=[DocumentResponse.from_document(doc, include_content=False) for doc in documents],
        page=page,
        page_size=effective_size,
        total=total,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get a document by id",
)
async def get_document(
    services: ServicesDep,
    document_id: str,
    include_content: Annotated[bool, Query()] = True,
) -> DocumentResponse:
    document = await service.get_document(services, document_id)
    return DocumentResponse.from_document(document, include_content=include_content)


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatusResponse,
    summary="Get a document's processing status",
)
async def get_document_status(services: ServicesDep, document_id: str) -> DocumentStatusResponse:
    document = await service.get_document(services, document_id)
    return DocumentStatusResponse(
        document_id=document.document_id, status=document.status, error=document.error
    )


@router.get(
    "/{document_id}/file",
    summary="Download the original document binary",
    response_class=Response,
)
async def download_document_file(services: ServicesDep, document_id: str) -> Response:
    document = await service.get_document(services, document_id)
    data = await services.minio.get_object(document_id)
    return Response(
        content=data,
        media_type=document.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{document.title}"',
        },
    )


@router.put(
    "/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadAcceptedResponse,
    summary="Replace a document (delete + re-create)",
)
async def replace_document(
    services: ServicesDep,
    document_id: str,
    file: Annotated[UploadFile, File(description="The new document binary.")],
) -> UploadAcceptedResponse:
    data = await file.read()
    document = await service.replace_document(
        services,
        document_id,
        data=data,
        filename=file.filename or "untitled",
        content_type=file.content_type or _DEFAULT_CONTENT_TYPE,
    )
    return UploadAcceptedResponse(
        document_id=document.document_id, status=document.status, title=document.title
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its chunks",
)
async def delete_document(services: ServicesDep, document_id: str) -> None:
    await service.delete_document(services, document_id)
