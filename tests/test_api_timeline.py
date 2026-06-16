"""API tests for the timeline endpoints (Task 9+10)."""

from __future__ import annotations

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
from saga.core.models import Event, EventCategory, EventType
from saga.events import EventRecorder, TimelineService
from saga.storage.postgres import PostgresStore

TEST_TOKEN = "test-token"


# --------------------------------------------------------------------------- #
# env fixture (required for load_config / AppConfig validators)                #
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
    """File-backed SQLite PostgresStore for timeline tests (NullPool-safe)."""
    tmp = Path(tempfile.mkdtemp()) / "timeline-test.sqlite"
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
        minio=None,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        search=None,  # type: ignore[arg-type]
        events=EventRecorder(store),
        timeline=TimelineService(store),
    )


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _audit_event(
    *,
    document_id: str = "d1",
    folder_id: str = "f1",
    summary: str = "Placed in Finance",
) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id="",  # store assigns
        category=EventCategory.AUDIT,
        event_type=EventType.PLACEMENT,
        document_id=document_id,
        folder_id=folder_id,
        occurred_at=now,
        recorded_at=now,
        actor="pipeline",
        summary=summary,
        dedupe_key=None,
    )


# --------------------------------------------------------------------------- #
# Tests                                                                         #
# --------------------------------------------------------------------------- #


async def test_get_timeline_returns_event(
    store: PostgresStore,
    services: Services,
    auth_headers: dict[str, str],
) -> None:
    await store.append_event(_audit_event(document_id="d1"))

    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        response = client.get("/timeline?document_id=d1", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["event_type"] == "placement"
    assert item["summary"] == "Placed in Finance"


async def test_get_document_timeline_returns_event(
    store: PostgresStore,
    services: Services,
    auth_headers: dict[str, str],
) -> None:
    await store.append_event(_audit_event(document_id="d1"))

    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        response = client.get("/documents/d1/timeline", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["event_type"] == "placement"


async def test_timeline_requires_auth(
    store: PostgresStore,
    services: Services,
) -> None:
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert client.get("/timeline").status_code == 401
        assert client.get("/documents/d1/timeline").status_code == 401
