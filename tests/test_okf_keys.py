from saga.okf_keys import RESERVED_FRONTMATTER_KEYS, is_reserved_key, metadata_from_frontmatter


def test_reserved_set_contains_the_okf_standard_keys() -> None:
    assert {"type", "title", "description", "tags", "resource", "timestamp"} <= (
        RESERVED_FRONTMATTER_KEYS
    )


def test_is_reserved_key_covers_reserved_names_and_saga_prefix() -> None:
    assert is_reserved_key("type")
    assert is_reserved_key("saga_id")
    assert is_reserved_key("saga_anything")
    assert not is_reserved_key("project")
    assert not is_reserved_key("author")


def test_metadata_from_frontmatter_keeps_only_unreserved_keys_as_strings() -> None:
    fm = {
        "type": "invoice",
        "title": "X",
        "saga_id": "d1",
        "project": "Apollo",
        "priority": 3,          # coerced to str
        "approved": True,       # coerced to str
    }
    assert metadata_from_frontmatter(fm) == {
        "project": "Apollo",
        "priority": "3",
        "approved": "True",
    }
