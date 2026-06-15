"""Folder management endpoints (FR-16/22): full CRUD, notes, and browsing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from saga.api import service
from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import (
    DocumentListResponse,
    DocumentResponse,
    FolderCreate,
    FolderUpdate,
    NoteCreate,
    NoteUpdate,
)
from saga.core.models import Folder, FolderNode, Note

router = APIRouter(prefix="/folders", tags=["folders"], dependencies=[AuthDep])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Folder,
    summary="Create a folder",
)
async def create_folder(services: ServicesDep, body: FolderCreate) -> Folder:
    return await service.create_folder(
        services,
        name=body.name,
        description=body.description,
        parent_id=body.parent_id,
        metadata=body.metadata,
        emoji=body.emoji,
    )


@router.get("", response_model=list[FolderNode], summary="Get the folder tree")
async def get_folder_tree(
    services: ServicesDep,
    prefix: Annotated[str | None, Query(description="Subtree rooted at this folder id.")] = None,
    max_depth: Annotated[int | None, Query(ge=1)] = None,
) -> list[FolderNode]:
    return await service.folder_tree(services, prefix=prefix, max_depth=max_depth)


@router.get("/flat", response_model=list[Folder], summary="List all folders (flat)")
async def list_folders_flat(services: ServicesDep) -> list[Folder]:
    return await services.db.list_folders()


@router.get("/{folder_id}", response_model=Folder, summary="Get a folder by id")
async def get_folder(services: ServicesDep, folder_id: str) -> Folder:
    return await service.get_folder(services, folder_id)


@router.patch(
    "/{folder_id}",
    response_model=Folder,
    summary="Update a folder (rename, move, describe, metadata)",
)
async def update_folder(services: ServicesDep, folder_id: str, body: FolderUpdate) -> Folder:
    fields = body.model_dump(exclude_unset=True)
    return await service.update_folder(services, folder_id, fields=fields)


@router.delete(
    "/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a folder (strategy: reject|reparent|cascade)",
)
async def delete_folder(
    services: ServicesDep,
    folder_id: str,
    strategy: Annotated[str, Query(pattern="^(reject|reparent|cascade)$")] = "reject",
) -> None:
    await service.delete_folder(services, folder_id, strategy=strategy)


@router.get(
    "/{folder_id}/documents",
    response_model=DocumentListResponse,
    summary="List documents in a folder branch",
)
async def documents_in_folder(
    services: ServicesDep,
    folder_id: str,
    include_subtree: Annotated[bool, Query()] = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    pagination = services.config.api.pagination
    effective_size = min(page_size or pagination.default_page_size, pagination.max_page_size)
    documents, total = await services.search.list_documents_in_folder(
        folder_id=folder_id,
        include_subtree=include_subtree,
        page=page,
        page_size=effective_size,
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_document(doc, include_content=False) for doc in documents],
        page=page,
        page_size=effective_size,
        total=total,
    )


# --------------------------------------------------------------------------- #
# Folder notes                                                                  #
# --------------------------------------------------------------------------- #


@router.get("/{folder_id}/notes", response_model=list[Note], summary="List a folder's notes")
async def list_folder_notes(services: ServicesDep, folder_id: str) -> list[Note]:
    folder = await service.get_folder(services, folder_id)
    return folder.notes


@router.post(
    "/{folder_id}/notes",
    status_code=status.HTTP_201_CREATED,
    response_model=Note,
    summary="Add a note to a folder",
)
async def add_folder_note(services: ServicesDep, folder_id: str, body: NoteCreate) -> Note:
    return await service.add_folder_note(services, folder_id, body.content)


@router.patch("/{folder_id}/notes/{note_id}", response_model=Note, summary="Update a folder note")
async def update_folder_note(
    services: ServicesDep, folder_id: str, note_id: str, body: NoteUpdate
) -> Note:
    return await service.update_folder_note(services, note_id, body.content)


@router.delete(
    "/{folder_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a folder note",
)
async def delete_folder_note(services: ServicesDep, folder_id: str, note_id: str) -> None:
    await service.delete_folder_note(services, note_id)
