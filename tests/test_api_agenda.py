"""API tests for GET /agenda (expanded upcoming events)."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
from tests.conftest import InMemoryBinaryStore

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
    tmp = Path(tempfile.mkdtemp()) / "agenda.sqlite"
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
        timeline=TimelineService(
            store,
            recurrence_horizon_days=config.timeline.recurrence_horizon_days,
            max_occurrences_per_rule=config.timeline.max_occurrences_per_rule,
        ),
    )


async def _seed(store: PostgresStore) -> None:
    now = datetime.now(UTC)
    await store.append_event(
        Event(
            event_id="rule-1",
            category=EventCategory.CONTENT,
            event_type=EventType.RECURRING,
            document_id="d1",
            occurred_at=now - timedelta(days=400),
            recorded_at=now - timedelta(days=400),
            actor="llm",
            summary="Annual premium",
            details={"source_quote": "q", "recurrence": "FREQ=YEARLY"},
        )
    )
    await store.append_event(
        Event(
            event_id="appt-1",
            category=EventCategory.CONTENT,
            event_type=EventType.APPOINTMENT,
            document_id="d1",
            occurred_at=now + timedelta(days=30),
            recorded_at=now,
            actor="llm",
            summary="Dentist",
        )
    )


async def test_agenda_returns_upcoming_expanded(store: PostgresStore, services: Services) -> None:
    await _seed(store)
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.get("/agenda", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    ids = [item["event_id"] for item in body["items"]]
    assert "appt-1" in ids
    assert any(i.startswith("rule-1@") for i in ids)
    assert "rule-1" not in ids
    dates = [item["occurred_at"] for item in body["items"]]
    assert dates == sorted(dates)


async def test_agenda_requires_auth(services: Services) -> None:
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert client.get("/agenda").status_code == 401
