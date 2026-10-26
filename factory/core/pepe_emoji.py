"""Bundled animated reactions, addressed by stable IDs rather than remote URLs."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .tool_registry import registry, tool_error, tool_result

ASSET_ROOT = Path(__file__).resolve().parents[1] / "web_ui" / "static" / "emoji"
_ID = re.compile(r"pepe_[a-z_]{1,32}")


@lru_cache(maxsize=1)
def _catalog() -> dict:
    raw = (ASSET_ROOT / "catalog.json").read_bytes()
    if len(raw) > 65536:
        raise ValueError("Emoji catalog is too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Invalid emoji catalog")
    rows = value.get("emojis")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise ValueError("Invalid emoji catalog entries")
    ids = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not _ID.fullmatch(row["id"])
            or row["id"] in ids
        ):
            raise ValueError("Invalid emoji ID")
        ids.add(row["id"])
        if row.get("file") != "pepe/" + row["id"] + ".gif":
            raise ValueError("Invalid emoji asset path")
        if not isinstance(row.get("sha256"), str) or not re.fullmatch(
            "[0-9a-f]{64}", row["sha256"]
        ):
            raise ValueError("Invalid emoji asset digest")
        if not isinstance(row.get("label"), str) or not 1 <= len(row["label"]) <= 80:
            raise ValueError("Invalid emoji label")
    return value


def catalog() -> dict:
    return json.loads(json.dumps(_catalog()))


def emoji_media(emoji_id: str) -> bytes:
    if not isinstance(emoji_id, str) or not _ID.fullmatch(emoji_id):
        raise ValueError("Unknown emoji")
    row = next((row for row in _catalog()["emojis"] if row["id"] == emoji_id), None)
    if row is None:
        raise ValueError("Unknown emoji")
    path = ASSET_ROOT / row["file"]
    if path.is_symlink():
        raise ValueError("Invalid emoji asset")
    with path.open("rb") as stream:
        data = stream.read(524289)
    if (
        not 0 < len(data) <= 524288
        or data[:6] not in {b"GIF87a", b"GIF89a"}
        or hashlib.sha256(data).hexdigest() != row["sha256"]
    ):
        raise ValueError("Invalid emoji asset")
    return data


def emoji_guidance() -> str:
    entries = ", ".join(
        ":" + row["id"] + ": (" + row["label"] + ")" for row in _catalog()["emojis"]
    )
    return (
        "Optional animated Pepe emoji: "
        + entries
        + ". Use a matching token sparingly in ordinary replies when the tone fits. Never put emoji tokens inside code, file paths, commands, or URLs."
    )


def render_emoji(arguments, **kwargs):
    if not isinstance(arguments, dict) or set(arguments) != {"emoji_id"}:
        return tool_error(
            "Supply exactly emoji_id from the available catalog.",
            reason_code="invalid_emoji",
        )
    identity = arguments["emoji_id"]
    try:
        emoji_media(identity)
        row = next(row for row in _catalog()["emojis"] if row["id"] == identity)
    except (OSError, ValueError, TypeError):
        return tool_error(
            "Emoji unavailable; choose a known catalog ID.", reason_code="invalid_emoji"
        )
    return tool_result(
        ok=True,
        kind="igorek.emoji",
        schema_version=1,
        emoji_id=identity,
        label=row["label"],
        token=":" + identity + ":",
    )


SCHEMA = {
    "name": "render_emoji",
    "description": "Show a small animated Pepe reaction in the chat. Use sparingly when appropriate or requested. In ordinary answer text you can instead insert the matching :pepe_name: token. Available reactions: bongo, think, happy_clap, happy_dance, laugh, love, give_love, happy_blanket, clap, cry, sad_rain, sad_vibes, sleep, sip_spin, no_u, brain_scratch. IDs use the pepe_ prefix. Never invent IDs or write HTML. Do not repeat the JSON result in your answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "emoji_id": {"type": "string", "pattern": "^pepe_[a-z_]{1,32}$"}
        },
        "required": ["emoji_id"],
        "additionalProperties": False,
    },
}
registry.register(
    name="render_emoji", toolset="session", schema=SCHEMA, handler=render_emoji
)
