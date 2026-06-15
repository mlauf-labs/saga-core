"""Document management endpoints (FR-1, FR-10, FR-11, FR-12, FR-28)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from saga.api import service
from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import (
    DocumentListResponse,
    DocumentPatch,
    DocumentResponse,
    DocumentSearchRequest,
    DocumentStatusResponse,
    MembershipResponse,
    MembershipSetRequest,
    NoteCreate,
    NoteUpdate,
    UploadAcceptedResponse,
)
from saga.core.models import Note

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
        document_id=document.document_id,
        status=document.status,
        title=document.title,
        filename=document.filename,
    )


@router.get("", response_model=DocumentListResponse, summary="List documents (paginated)")
async def list_documents(
    services: ServicesDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    pagination = services.config.api.pagination
    effective_size = min(page_size or pagination.default_page_size, pagination.max_page_size)
    documents, total = await services.db.list_documents(page=page, page_size=effective_size)
    return DocumentListResponse(
        items=[DocumentResponse.from_document(doc, include_content=False) for doc in documents],
        page=page,
        page_size=effective_size,
        total=total,
    )


@router.post(
    "/search",
    response_model=DocumentListResponse,
    summary="Keyword search over documents (title, summary, content, metadata) with filters",
)
async def search_documents(
    services: ServicesDep, request: DocumentSearchRequest
) -> DocumentListResponse:
    pagination = services.config.api.pagination
    effective_size = min(
        request.page_size or pagination.default_page_size, pagination.max_page_size
    )
    documents, total = await services.search.search_documents(
        query=request.query,
        page=request.page,
        page_size=effective_size,
        doc_type=request.doc_type,
        folder_id=request.folder_id,
        include_subtree=request.include_subtree,
        title=request.title,
        status=request.status,
        filters=request.filters or None,
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_document(doc, include_content=False) for doc in documents],
        page=request.page,
        page_size=effective_size,
        total=total,
    )


@router.get("/{document_id}", response_model=DocumentResponse, summary="Get a document by id")
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
    summary="Download or inline-preview the original document binary",
    response_class=Response,
)
async def download_document_file(
    services: ServicesDep,
    document_id: str,
    disposition: Annotated[str, Query(pattern="^(inline|attachment)$")] = "attachment",
) -> Response:
    document = await service.get_document(services, document_id)
    data = await services.minio.get_object(document_id)
    return Response(
        content=data,
        media_type=document.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'{disposition}; filename="{document.title}"'},
    )


@router.post(
    "/{document_id}/reanalyze",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadAcceptedResponse,
    summary="Re-run analysis and regenerate metadata for an existing document",
)
async def reanalyze_document(services: ServicesDep, document_id: str) -> UploadAcceptedResponse:
    document = await service.reanalyze_document(services, document_id)
    return UploadAcceptedResponse(
        document_id=document.document_id,
        status=document.status,
        title=document.title,
        filename=document.filename,
    )


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Update editable document fields (title, summary, doc_type, values)",
)
async def update_document(
    services: ServicesDep, document_id: str, patch: DocumentPatch
) -> DocumentResponse:
    document = await service.update_document(services, document_id, patch)
    return DocumentResponse.from_document(document, include_content=False)


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
        document_id=document.document_id,
        status=document.status,
        title=document.title,
        filename=document.filename,
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its chunks",
)
async def delete_document(services: ServicesDep, document_id: str) -> None:
    await service.delete_document(services, document_id)


# --------------------------------------------------------------------------- #
# Document notes                                                                #
# --------------------------------------------------------------------------- #


@router.get("/{document_id}/notes", response_model=list[Note], summary="List a document's notes")
async def list_document_notes(services: ServicesDep, document_id: str) -> list[Note]:
    document = await service.get_document(services, document_id)
    return document.notes


@router.post(
    "/{document_id}/notes",
    status_code=status.HTTP_201_CREATED,
    response_model=Note,
    summary="Add a note to a document",
)
async def add_document_note(services: ServicesDep, document_id: str, body: NoteCreate) -> Note:
    return await service.add_document_note(services, document_id, body.content)


@router.patch(
    "/{document_id}/notes/{note_id}", response_model=Note, summary="Update a document note"
)
async def update_document_note(
    services: ServicesDep, document_id: str, note_id: str, body: NoteUpdate
) -> Note:
    return await service.update_document_note(services, note_id, body.content)


@router.delete(
    "/{document_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document note",
)
async def delete_document_note(services: ServicesDep, document_id: str, note_id: str) -> None:
    await service.delete_document_note(services, note_id)


# --------------------------------------------------------------------------- #
# Document <-> folder membership                                                #
# --------------------------------------------------------------------------- #


@router.get(
    "/{document_id}/folders",
    response_model=MembershipResponse,
    summary="List the folders a document belongs to",
)
async def list_document_folders(services: ServicesDep, document_id: str) -> MembershipResponse:
    document = await service.get_document(services, document_id)
    return MembershipResponse(folders=document.folders)


@router.put(
    "/{document_id}/folders",
    response_model=MembershipResponse,
    summary="Replace a document's folder membership set",
)
async def set_document_folders(
    services: ServicesDep, document_id: str, body: MembershipSetRequest
) -> MembershipResponse:
    refs = await service.set_document_folders(
        services, document_id, folder_ids=body.folder_ids, primary_id=body.primary_id
    )
    return MembershipResponse(folders=refs)


@router.post(
    "/{document_id}/folders/{folder_id}",
    response_model=MembershipResponse,
    summary="Add a document to a folder",
)
async def add_document_folder(
    services: ServicesDep,
    document_id: str,
    folder_id: str,
    primary: Annotated[bool, Query()] = False,
) -> MembershipResponse:
    refs = await service.add_document_folder(services, document_id, folder_id, primary=primary)
    return MembershipResponse(folders=refs)


@router.patch(
    "/{document_id}/folders/{folder_id}",
    response_model=MembershipResponse,
    summary="Mark a folder as the document's primary folder",
)
async def set_primary_folder(
    services: ServicesDep, document_id: str, folder_id: str
) -> MembershipResponse:
    refs = await service.set_primary_folder(services, document_id, folder_id)
    return MembershipResponse(folders=refs)


@router.delete(
    "/{document_id}/folders/{folder_id}",
    response_model=MembershipResponse,
    summary="Remove a document from a folder",
)
async def remove_document_folder(
    services: ServicesDep, document_id: str, folder_id: str
) -> MembershipResponse:
    refs = await service.remove_document_folder(services, document_id, folder_id)
    return MembershipResponse(folders=refs)
