"""API test for POST /import/okf (round-trips a bundle the exporter produced)."""

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
from saga.core.models import Document, DocumentStatus
from saga.events import EventRecorder, TimelineService
from saga.export.okf import OkfBundleBuilder
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeQueue, InMemoryBinaryStore

TEST_TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", TEST_TOKEN)
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


@pytest_asyncio.fixture
async def store() -> AsyncIterator[PostgresStore]:
    tmp = Path(tempfile.mkdtemp()) / "okf-import-api.sqlite"
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
        queue=FakeQueue(),
        search=None,  # type: ignore[arg-type]
        events=EventRecorder(store),
        timeline=TimelineService(store),
    )


async def _bundle_bytes(store: PostgresStore, minio: InMemoryBinaryStore) -> bytes:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    folder = await store.create_folder(name="Finanzen", description="Money", emoji="💰")
    await store.create_doc_type(name="invoice", description="A bill.", emoji="📄")
    await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung",
            filename="r.md",
            mime_type="text/markdown",
            size_bytes=6,
            content_hash="h",
            minio_object="saga-originals/d1",
            status=DocumentStatus.READY,
            doc_type="invoice",
            summary="One invoice.",
            content_markdown="# Body",
            created_at=now,
            updated_at=now,
        )
    )
    await store.set_document_folders(
        "d1", folder_ids=[folder.folder_id], primary_id=folder.folder_id
    )
    builder = OkfBundleBuilder(
        db=store,
        minio=minio,
        timeline=TimelineService(store),
        store_name="saga",
        public_base_url=None,
        with_originals=False,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    return buf.getvalue()


async def test_import_okf_restores_bundle(store: PostgresStore, services: Services) -> None:
    src_engine = create_async_engine("sqlite+aiosqlite://")
    src = PostgresStore(config=None, engine=src_engine)  # type: ignore[arg-type]
    await src.bootstrap()
    try:
        bundle = await _bundle_bytes(src, InMemoryBinaryStore())
    finally:
        await src.close()

    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.post(
            "/import/okf",
            headers=AUTH,
            files={"file": ("bundle.tar.gz", bundle, "application/gzip")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["documents_imported"] == 1
    assert body["folders_created"] == 1
    assert await store.get_document("d1") is not None


async def test_import_okf_requires_auth(services: Services) -> None:
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert client.post("/import/okf").status_code == 401
