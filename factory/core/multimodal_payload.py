"""Assembly of multimodal content blocks for the Anthropic Messages API."""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _image_media_type(path: Path) -> str | None:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed in SUPPORTED_IMAGE_MIME_TYPES:
        return guessed
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    return None


def _image_block(path: str) -> dict[str, Any] | None:
    image_path = Path(path).expanduser()
    if not image_path.is_file():
        return None
    media_type = _image_media_type(image_path)
    if media_type is None:
        return None
    if os.path.getsize(image_path) > MAX_IMAGE_BYTES:
        return None
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def build_anthropic_user_content(
    text: str,
    *,
    input_files: list[str] | None = None,
) -> str | list[dict[str, Any]]:
    """Return user content with image blocks if image files are present in input_files."""
    blocks: list[dict[str, Any]] = []
    normalized_text = str(text or "")
    if normalized_text:
        blocks.append({"type": "text", "text": normalized_text})

    image_count = 0
    skipped: list[str] = []
    for raw_path in input_files or []:
        block = _image_block(str(raw_path))
        if block is None:
            skipped.append(str(raw_path))
            continue
        blocks.append(block)
        image_count += 1

    if image_count == 0:
        return normalized_text
    if skipped:
        blocks.append(
            {
                "type": "text",
                "text": "Некоторые вложения не были встроены как изображения: " + ", ".join(skipped),
            }
        )
    return blocks
