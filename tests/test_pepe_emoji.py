"""Bundled reactions and model contracts use owned files and no external services."""

import hashlib
import json

import pytest

from factory.core import pepe_emoji as e
from factory.core.prompt_assembly import build_cc_backend_system_prompt

SOURCE_CATALOG = e.catalog()


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    e._catalog.cache_clear()
    yield
    e._catalog.cache_clear()


@pytest.mark.parametrize("row", SOURCE_CATALOG["emojis"], ids=lambda row: row["id"])
def test_bundled_gif_and_tool_result_are_identified_by_same_stable_id(row):
    data = e.emoji_media(row["id"])
    assert len(data) == row["bytes"] and data[:6] in [b"GIF87a", b"GIF89a"]
    assert hashlib.sha256(data).hexdigest() == row["sha256"] and row["frames"] > 1
    result = json.loads(e.render_emoji({"emoji_id": row["id"]}))
    assert result == {
        "ok": True,
        "kind": "igorek.emoji",
        "schema_version": 1,
        "emoji_id": row["id"],
        "label": row["label"],
        "token": ":" + row["id"] + ":",
    }


def test_public_catalog_is_copied_and_has_no_owner_paths():
    first = e.catalog()
    first["emojis"].clear()
    assert len(e.catalog()["emojis"]) == 16
    assert "/home/" not in json.dumps(e.catalog()) and "/tmp/" not in json.dumps(
        e.catalog()
    )


@pytest.mark.parametrize(
    "identity",
    [
        "../credentials",
        "https://example.invalid/private",
        "pepe_unknown",
        "pepe_UPPER",
        "",
        None,
        [],
        4,
    ],
)
def test_unknown_id_never_becomes_a_file_or_network_request(identity):
    with pytest.raises(ValueError, match="Unknown emoji"):
        e.emoji_media(identity)
    assert "error" in json.loads(e.render_emoji({"emoji_id": identity}))


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        {},
        {"emoji_id": "pepe_think", "url": "https://example.invalid"},
        {"emoji": "pepe_think"},
    ],
)
def test_tool_refuses_wrong_shape_and_extra_url(arguments):
    assert "error" in json.loads(e.render_emoji(arguments))


@pytest.mark.parametrize(
    "kind", ["digest", "signature", "oversized", "symlink", "missing"]
)
def test_modified_owned_asset_is_refused(tmp_path, monkeypatch, kind):
    row = dict(SOURCE_CATALOG["emojis"][0])
    root = tmp_path / "emoji"
    target = root / row["file"]
    target.parent.mkdir(parents=True)
    data = e.emoji_media(row["id"])
    if kind == "signature":
        data = b"notgif" + data[6:]
        row["sha256"] = hashlib.sha256(data).hexdigest()
    elif kind == "digest":
        data = data[:-1] + bytes([data[-1] ^ 1])
    elif kind == "oversized":
        data = b"GIF89a" + b"x" * 524289
        row["sha256"] = hashlib.sha256(data).hexdigest()
    if kind == "symlink":
        other = tmp_path / "outside.gif"
        other.write_bytes(data)
        target.symlink_to(other)
    elif kind != "missing":
        target.write_bytes(data)
    (root / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "emojis": [row]})
    )
    monkeypatch.setattr(e, "ASSET_ROOT", root)
    e._catalog.cache_clear()
    assert "error" in json.loads(e.render_emoji({"emoji_id": row["id"]}))


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"schema_version": 2, "emojis": []},
        {"schema_version": 1, "emojis": []},
        {"schema_version": 1, "emojis": [None]},
        {"schema_version": 1, "emojis": [{"id": "../x"}]},
    ],
)
def test_invalid_catalog_has_explicit_failure(tmp_path, monkeypatch, value):
    (tmp_path / "catalog.json").write_text(json.dumps(value))
    monkeypatch.setattr(e, "ASSET_ROOT", tmp_path)
    with pytest.raises(ValueError):
        e.catalog()


def test_duplicate_ids_invalid_paths_digests_and_labels_are_refused(
    tmp_path, monkeypatch
):
    row = SOURCE_CATALOG["emojis"][0]
    monkeypatch.setattr(e, "ASSET_ROOT", tmp_path)
    for rows in [
        [row, row],
        [{**row, "file": "../outside.gif"}],
        [{**row, "sha256": "bad"}],
        [{**row, "label": None}],
    ]:
        (tmp_path / "catalog.json").write_text(
            json.dumps({"schema_version": 1, "emojis": rows})
        )
        e._catalog.cache_clear()
        with pytest.raises(ValueError):
            e.catalog()


@pytest.mark.parametrize("style", ["full", "compact"])
@pytest.mark.parametrize("platform", ["desktop", "android", "mobile", None, "telegram"])
def test_reaction_guidance_is_limited_to_supported_chat_surfaces(
    tmp_path, style, platform
):
    prompt = build_cc_backend_system_prompt(
        workdir=str(tmp_path),
        filesystem_mode="workspace",
        prompt_style=style,
        source_platform=platform,
    )
    assert ("Optional animated Pepe emoji:" in prompt) is (
        platform in {"desktop", "android", "mobile"}
    )
    if platform in {"desktop", "android", "mobile"}:
        assert (
            ":pepe_think:" in prompt
            and "sparingly" in prompt
            and "file paths" in prompt
        )
