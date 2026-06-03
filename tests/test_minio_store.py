"""Unit tests for the MinIO store using a mocked client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from docstore.core.config import MinioConfig
from docstore.core.errors import StorageError
from docstore.storage.minio import MinioStore


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.bucket_exists.return_value = False
    return client


@pytest.fixture
def store(fake_client: MagicMock) -> MinioStore:
    return MinioStore(MinioConfig(bucket="docstore-originals"), client=fake_client)


async def test_bootstrap_creates_missing_bucket(store: MinioStore, fake_client: MagicMock) -> None:
    await store.bootstrap()
    fake_client.make_bucket.assert_called_once_with("docstore-originals")


async def test_bootstrap_skips_existing_bucket(store: MinioStore, fake_client: MagicMock) -> None:
    fake_client.bucket_exists.return_value = True
    await store.bootstrap()
    fake_client.make_bucket.assert_not_called()


async def test_put_object_returns_key(store: MinioStore, fake_client: MagicMock) -> None:
    key = await store.put_object("d1", b"hello", "application/pdf")
    assert key == "docstore-originals/d1"
    fake_client.put_object.assert_called_once()


async def test_get_object_reads_and_closes(store: MinioStore, fake_client: MagicMock) -> None:
    response = MagicMock()
    response.read.return_value = b"data"
    fake_client.get_object.return_value = response
    assert await store.get_object("d1") == b"data"
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


async def test_remove_object(store: MinioStore, fake_client: MagicMock) -> None:
    await store.remove_object("d1")
    fake_client.remove_object.assert_called_once_with("docstore-originals", "d1")


async def test_errors_wrapped(store: MinioStore, fake_client: MagicMock) -> None:
    fake_client.put_object.side_effect = OSError("disk full")
    with pytest.raises(StorageError):
        await store.put_object("d1", b"x", "text/plain")


def test_client_lazy_build() -> None:
    store = MinioStore(MinioConfig(endpoint="localhost:9000", access_key="a", secret_key="b"))
    assert store.client is not None
    assert store.bucket == "docstore-originals"
