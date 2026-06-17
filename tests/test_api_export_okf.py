"""API tests for the GET /export/okf streaming OKF bundle endpoint (Task 6)."""

from __future__ import annotations

import io
import tarfile
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from saga.api.app import create_app
from saga.api.dependencies import Services
from saga.core.config import AppConfig
from saga.core.models import Document
from saga.events import EventRecorder, TimelineService
from saga.storage.postgres import PostgresStore
from tests.conftest import InMemoryBinaryStore

TEST_TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


# --------------------------------------------------------------------------- #
# env fixture (required for AppConfig validators)                              #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", TEST_TOKEN)
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def store() -> AsyncIterator[PostgresStore]:
    """File-backed SQLite PostgresStore (NullPool-safe)."""
    tmp = Path(tempfile.mkdtemp()) / "okf-api-test.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}", poolclass=NullPool)
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    try:
        yield s
    finally:
        await s.close()
        tmp.unlink(missing_ok=True)


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.security.bearer_tokens = TEST_TOKEN
    return cfg


@pytest.fixture
def services(store: PostgresStore, config: AppConfig) -> Services:
    return Services(
        config=config,
        db=store,
        opensearch=None,  # type: ignore[arg-type]
        minio=InMemoryBinaryStore(),
        queue=None,  # type: ignore[arg-type]
        search=None,  # type: ignore[arg-type]
        events=EventRecorder(store),
        timeline=TimelineService(store),
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


async def _seed(store: PostgresStore) -> None:
    """Seed one folder and one document so the bundle is non-empty."""
    now = datetime(2026, 5, 1, tzinfo=UTC)
    folder = await store.create_folder(name="Finanzen", description="Money")
    doc = await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung ACME",
            filename="rechnung.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            content_hash="h",
            minio_object="saga-originals/d1",
            doc_type="invoice",
            summary="One invoice.",
            content_markdown="# Body",
            created_at=now,
            updated_at=now,
        )
    )
    await store.set_document_folders(
        doc.document_id, folder_ids=[folder.folder_id], primary_id=folder.folder_id
    )


# --------------------------------------------------------------------------- #
# Tests                                                                         #
# --------------------------------------------------------------------------- #


async def test_export_okf_streams_a_bundle(
    store: PostgresStore,
    services: Services,
) -> None:
    await _seed(store)
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.get("/export/okf", headers=AUTH)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gzip")
    assert "content-disposition" in resp.headers
    assert resp.headers["content-disposition"].startswith("attachment; filename=")

    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        root = names[0].split("/")[0]
        assert f"{root}/index.md" in names
        # At least one concept file (ends with .md and contains __ separator from backup_basename)
        assert any(n.endswith(".md") and "__" in n for n in names)


async def test_export_okf_requires_auth(
    services: Services,
) -> None:
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert client.get("/export/okf").status_code == 401


async def test_export_okf_with_originals_param(
    store: PostgresStore,
    services: Services,
) -> None:
    await _seed(store)
    # Store a fake binary so get_object won't KeyError with with_originals=true
    services.minio.objects["d1"] = b"PDFBYTES"  # type: ignore[attr-defined]
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.get("/export/okf?with_originals=true", headers=AUTH)

    assert resp.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        assert any(n.endswith(".pdf") for n in tar.getnames())


async def test_export_okf_bundle_contains_machine_readable_files(
    store: PostgresStore,
    services: Services,
) -> None:
    await _seed(store)
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.get("/export/okf", headers=AUTH)

    assert resp.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        root = names[0].split("/")[0]
        assert f"{root}/saga-manifest.json" in names
        assert f"{root}/saga-events.jsonl" in names
