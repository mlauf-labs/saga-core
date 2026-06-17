"""Backup/export endpoints: document cursor-pagination (FR-28) and OKF bundle (FR-OKF)."""

from __future__ import annotations

import tarfile
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import DocumentResponse, ExportPageResponse
from saga.core.errors import SagaError
from saga.export import OkfBundleBuilder

if TYPE_CHECKING:
    from collections.abc import Iterator

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


@router.get("/okf", summary="Export the whole archive as an OKF .tar.gz bundle")
async def export_okf(
    services: ServicesDep,
    with_originals: Annotated[bool, Query(description="Include original binaries.")] = False,
) -> StreamingResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    builder = OkfBundleBuilder(
        db=services.db,
        minio=services.minio,
        timeline=services.timeline,
        store_name=services.config.name,
        public_base_url=services.config.export.public_base_url,
        with_originals=with_originals,
    )
    tmp: SpooledTemporaryFile[bytes] = SpooledTemporaryFile(  # noqa: SIM115
        max_size=64 * 1024 * 1024, mode="w+b"
    )
    with tarfile.open(fileobj=tmp, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    tmp.seek(0)

    def _stream() -> Iterator[bytes]:
        try:
            while chunk := tmp.read(64 * 1024):
                yield chunk
        finally:
            tmp.close()

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"okf-{services.config.name}-{stamp}.tar.gz"
    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
