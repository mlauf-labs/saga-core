"""Import endpoint: restore an uploaded OKF .tar.gz bundle (faithful round-trip)."""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from saga.api.dependencies import AuthDep, ServicesDep
from saga.imports.okf import ImportSummary, OkfBundleImporter

router = APIRouter(prefix="/import", tags=["import"], dependencies=[AuthDep])


@router.post("/okf", summary="Import an OKF .tar.gz bundle (faithful round-trip)")
async def import_okf(
    services: ServicesDep,
    file: Annotated[UploadFile, File(description="The OKF .tar.gz bundle to import.")],
) -> ImportSummary:
    data = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "bundle"
        extract_dir.mkdir()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(extract_dir, filter="data")
        importer = OkfBundleImporter(
            db=services.db,
            minio=services.minio,
            queue=services.queue,
            config=services.config,
        )
        return await importer.run(extract_dir)
