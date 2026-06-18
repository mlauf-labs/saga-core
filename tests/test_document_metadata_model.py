from datetime import UTC, datetime

from saga.core.models import Document


def _doc(**kw: object) -> Document:
    base: dict[str, object] = {
        "document_id": "d1",
        "title": "t",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "content_hash": "h",
        "minio_object": "o",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


def test_metadata_defaults_to_empty_dict() -> None:
    assert _doc().metadata == {}


def test_metadata_round_trips_string_values() -> None:
    assert _doc(metadata={"project": "Apollo"}).metadata == {"project": "Apollo"}
