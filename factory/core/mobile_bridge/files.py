from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
from pathlib import Path
from typing import BinaryIO

from ..telegram_bridge import _collect_output_file_paths, _output_file_roots

MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024


def describe_file(path: Path) -> dict:
    info = path.stat()
    identity = f"{path}:{info.st_size}:{info.st_mtime_ns}"
    return {
        "id": hashlib.sha256(identity.encode()).hexdigest(),
        "name": path.name,
        "size": info.st_size,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "modified_at": int(info.st_mtime),
    }


def collect_files(base_dir: Path, text: str, explicit: list[str], task_id: str = "", *, visible_base: bool = False) -> list[tuple[dict, Path]]:
    roots = _output_file_roots(base_dir, task_id)
    found = []
    for raw in _collect_output_file_paths(base_dir, [text], explicit, allowed_roots=roots):
        path = Path(raw)
        checked_parts = path.parts
        if visible_base:
            try:
                checked_parts = path.relative_to(base_dir.resolve()).parts
            except ValueError:
                continue
        if any(part.startswith(".") for part in checked_parts):
            continue
        if path.suffix.lower() in {".pem", ".key", ".keystore", ".jks"}:
            continue
        try:
            description = describe_file(path)
            if description["size"] <= MAX_DOWNLOAD_BYTES:
                found.append((description, path))
        except OSError:
            continue
    return found


def open_file_safely(path: Path, expected: dict) -> BinaryIO:
    # Walk directory descriptors without following symlinks, including ancestors.
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    handle = os.fdopen(descriptor, "rb")
    try:
        info = os.fstat(handle.fileno())
        identity = f"{path}:{info.st_size}:{info.st_mtime_ns}"
        if not stat.S_ISREG(info.st_mode) or hashlib.sha256(identity.encode()).hexdigest() != expected["id"]:
            raise OSError("Файл изменился, обновите список вложений")
        return handle
    except BaseException:
        handle.close()
        raise


def download_range(header: str | None, size: int) -> tuple[int, int]:
    """Return an inclusive single byte range, rejecting invalid or unsatisfiable requests."""
    import re
    if not header:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip()) if len(header) <= 128 else None
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("invalid_byte_range")
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError("invalid_byte_range")
        return max(0, size - count), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("invalid_byte_range")
    return start, end
