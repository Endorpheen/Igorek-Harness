"""Store attachments uploaded by the mobile application.

Files live in the session workspace (cwd), rather than the data directory,
so profiles restricted to their workspace can read attachments. The session
metadata provides cwd independently of the selected profile.

The adjacent index.json maps upload IDs to metadata. Tasks can attach only
registered uploads, and resolved paths must stay inside the upload directory.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from ..artifact_files import atomic_write_file, open_regular_file, read_regular_text

UPLOAD_DIRNAME = ".igorek_uploads"
INDEX_NAME = "index.json"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".bmp",
}

_INDEX_LOCK = threading.RLock()


def public_attachments(attachments: object) -> list[dict[str, Any]]:
    if not isinstance(attachments, list):
        return []
    fields = ("upload_id", "name", "kind", "mime_type", "size")
    return [
        {key: item[key] for key in fields if key in item}
        for item in attachments
        if isinstance(item, dict) and re.fullmatch(r"[0-9a-f]{32}", str(item.get("upload_id") or ""))
    ]


def sanitize_upload_name(name: str, *, fallback: str = "upload") -> str:
    candidate = str(name or "").replace("\\", "/")
    candidate = candidate.split("/")[-1]
    candidate = re.sub(r"[\x00-\x1f\x7f]+", "_", candidate)
    candidate = re.sub(r"[^\w.\- ]+", "_", candidate, flags=re.UNICODE)
    candidate = candidate.strip(" ._")
    return (candidate or fallback)[:180]


def classify_upload_kind(name: str, mime_type: str) -> str:
    mime = str(mime_type or "").lower()
    suffix = Path(str(name or "")).suffix.lower()
    if mime.startswith("image/") or suffix in IMAGE_EXTENSIONS:
        return "image"
    return "file"


def session_upload_dir(cwd: str | os.PathLike[str], session_id: str) -> Path:
    identifier = str(session_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", identifier):
        raise ValueError("Invalid upload session identifier")
    return Path(cwd).expanduser().resolve() / UPLOAD_DIRNAME / identifier


def _index_path(directory: Path) -> Path:
    return directory / INDEX_NAME


def _load_index(directory: Path) -> dict[str, Any]:
    path = _index_path(directory)
    try:
        payload = json.loads(read_regular_text(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_index(directory: Path, index: dict[str, Any]) -> None:
    path = _index_path(directory)
    atomic_write_file(path, json.dumps(index, ensure_ascii=False, indent=2), mode=0o600)


def save_session_upload(
    cwd: str | os.PathLike[str],
    session_id: str,
    *,
    filename: str,
    mime_type: str,
    data: bytes,
) -> dict[str, Any]:
    payload = bytes(data or b"")
    if not payload:
        raise ValueError("вложение пустое")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("вложение слишком большое")

    directory = session_upload_dir(cwd, session_id)
    safe_name = sanitize_upload_name(filename)
    upload_id = uuid.uuid4().hex
    suffix = Path(safe_name).suffix
    target = directory / f"{upload_id}{suffix}"
    atomic_write_file(target, payload, mode=0o600, preserve_mode=False)

    descriptor = {
        "upload_id": upload_id,
        "name": safe_name,
        "kind": classify_upload_kind(safe_name, mime_type),
        "mime_type": str(mime_type or "application/octet-stream"),
        "size": len(payload),
        "path": str(target),
        "created_at": int(time.time()),
    }
    with _INDEX_LOCK:
        index = _load_index(directory)
        index[upload_id] = descriptor
        _save_index(directory, index)
    return descriptor


def resolve_session_uploads(
    cwd: str | os.PathLike[str],
    session_id: str,
    upload_ids: list[str] | None,
) -> list[dict[str, Any]]:
    normalized_ids = [
        str(item or "").strip() for item in (upload_ids or []) if str(item or "").strip()
    ]
    if not normalized_ids:
        return []

    directory = session_upload_dir(cwd, session_id)
    with _INDEX_LOCK:
        index = _load_index(directory)
    base = directory

    resolved: list[dict[str, Any]] = []
    for upload_id in normalized_ids:
        item = index.get(upload_id)
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            candidate = Path(os.path.abspath(raw_path))
            candidate.relative_to(base)
            with open_regular_file(candidate) as stream:
                size = int(item.get("size") or os.fstat(stream.fileno()).st_size)
        except (OSError, TypeError, ValueError):
            continue
        resolved.append(
            {
                "upload_id": upload_id,
                "name": str(item.get("name") or candidate.name),
                "kind": str(item.get("kind") or classify_upload_kind(candidate.name, "")),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "size": size,
                "path": str(candidate),
            }
        )
    return resolved


def format_attachments_block(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""
    lines = ["Вложения из мобильного приложения:"]
    for index, item in enumerate(attachments, start=1):
        kind = str(item.get("kind") or "file")
        name = str(item.get("name") or "file")
        lines.append(f"{index}. {kind}: {name}")
        lines.append(f"   path: {item.get('path')}")
    lines.append("")
    lines.append(
        "Изображения передаются модели как визуальный вход, если профиль это поддерживает — "
        "не читай картинки через Read. Остальные файлы читай штатными инструментами "
        "по указанному локальному пути."
    )
    return "\n".join(lines)
