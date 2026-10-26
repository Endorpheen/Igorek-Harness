"""Owned emoji registry persistence and Telegram UTF-16 entity boundaries."""

import json
from unittest.mock import Mock

import pytest

from factory.core import custom_emoji_registry as e


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "owned" / "emoji.json"
    monkeypatch.setattr(e, "REGISTRY_PATH", path)
    monkeypatch.setattr(e, "_registry", {})
    monkeypatch.setattr(e, "_next_alias_num", 1)
    return path


def message(text="A", offset=0, length=1, identity="100"):
    return {
        "text": text,
        "entities": [
            {
                "type": "custom_emoji",
                "custom_emoji_id": identity,
                "offset": offset,
                "length": length,
            }
        ],
    }


@pytest.mark.parametrize(
    "text,offset,length,expected",
    [
        ("A", 0, 1, "A"),
        ("x🐙y", 1, 2, "🐙"),
        ("🐙AB", 2, 1, "A"),
        ("🐙🦑", 2, 2, "🦑"),
        ("x✌️z", 1, 2, "✌️"),
        ("hello", 1, 3, "ell"),
    ],
)
def test_entity_fallback_matches_utf16_span(registry, text, offset, length, expected):
    row = e.register_from_json(message(text, offset, length))[0]
    assert row["fallback"] == expected
    assert json.loads(registry.read_text())["100"]["fallback"] == expected


@pytest.mark.parametrize(
    "offset,length",
    [
        (None, 1),
        ("0", 1),
        ([], 1),
        (True, 1),
        (-1, 1),
        (0, -1),
        (0, 0),
        (0, "1"),
        (0, None),
        (1, 1),
        (0, 1),
        (4, 2),
    ],
)
def test_invalid_or_split_surrogate_entity_is_not_registered(registry, offset, length):
    assert e.register_from_json(message("🐙", offset, length)) == []
    assert e.list_all() == [] and not registry.exists()


@pytest.mark.parametrize("entities", [None, {}, "invalid", 4])
def test_invalid_entity_collection_is_empty(entities):
    assert e.register_from_json({"text": "A", "entities": entities}) == []


def test_unknown_and_incomplete_entities_do_not_create_aliases():
    assert (
        e.register_from_json(
            {
                "text": "A",
                "entities": [
                    None,
                    1,
                    {},
                    {"type": "bold"},
                    {"type": "custom_emoji", "custom_emoji_id": " "},
                ],
            }
        )
        == []
    )
    assert e.format_for_prompt() == "" and e.get_emoji("absent") is None
    assert e.get_emoji_by_alias("absent") is None


def test_reload_skips_invalid_rows_and_keeps_alias_counter_above_saved_values(registry):
    registry.parent.mkdir()
    registry.write_text(
        json.dumps(
            {
                "100": {"id": "100", "alias": "custom_7", "fallback": "A"},
                "bad": [],
                "incomplete": {"alias": "bad"},
                "200": {"id": "200", "alias": "manual", "fallback": "B"},
            }
        )
    )
    e._load()
    assert len(e.list_all()) == 2
    row = e.register_from_json(message(identity="300"))[0]
    assert row["alias"] == "custom_8"
    assert "custom_7" in e.format_for_prompt() and "manual" in e.format_for_prompt()


@pytest.mark.parametrize("content", ["{", "[]", "null"])
def test_invalid_saved_json_does_not_rewrite_or_crash(registry, content):
    registry.parent.mkdir()
    registry.write_text(content)
    e._load()
    assert e.list_all() == [] and registry.read_text() == content


def test_missing_registry_is_not_created_by_read(registry):
    e._load()
    assert e.list_all() == [] and not registry.exists()


def test_save_failure_is_logged_without_losing_in_memory_entry(monkeypatch, caplog):
    path = Mock()
    path.write_text.side_effect = OSError("owned fixture read-only")
    monkeypatch.setattr(e, "REGISTRY_PATH", path)
    row = e.register_from_json(message())[0]
    assert e.get_emoji("100") is row
    assert "Failed to save" in caplog.text
