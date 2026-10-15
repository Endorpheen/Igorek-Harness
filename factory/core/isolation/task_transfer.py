"""Transfer oversized tasks through the existing workspace export."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .channel import ChannelConfig, ChannelError, JsonValue, MessageKind, payload_policy_violation

INLINE_TASK_BYTES = 1024 * 1024
MAX_TASK_BYTES = 128 * 1024 * 1024


def _bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _filename(task_id: str, digest: str) -> str:
    return "task-payload-" + hashlib.sha256(task_id.encode()).hexdigest()[:24] + "-" + digest + ".json"


def _validate(payload: Mapping[str, JsonValue]) -> None:
    reason = payload_policy_violation(MessageKind.TASK, payload, ChannelConfig())
    if reason:
        raise ChannelError(reason)


def pack_task(payload: dict[str, JsonValue], *, host_root: str) -> dict[str, JsonValue]:
    """Keep small tasks inline; publish large tasks atomically before sending."""
    body = _bytes(payload)
    if len(body) <= INLINE_TASK_BYTES:
        return payload
    _validate(payload)
    if len(body) > MAX_TASK_BYTES:
        raise ChannelError("task_payload_too_large", str(len(body)))
    # MCP environments may contain ephemeral credentials; keep them on the channel.
    stored = {key: value for key, value in payload.items() if key != "mcp_servers"}
    body = _bytes(stored)
    task_id = str(payload["task_id"])
    workdir = Path(str(payload["workdir"]))
    artifact = Path(str(payload["artifact_workdir"]))
    relative = artifact.relative_to(workdir)
    if relative != Path(".igorek-artifacts") / task_id[:8]:
        raise ChannelError("task_payload_path_invalid")
    root = Path(host_root).resolve()
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to(root):
        raise ChannelError("task_payload_path_invalid")
    digest = hashlib.sha256(body).hexdigest()
    filename = _filename(task_id, digest)
    fd, temporary = tempfile.mkstemp(prefix=".task-payload-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / filename)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "task_id": task_id, "workdir": str(workdir), "artifact_workdir": str(artifact),
        "task_payload_file": {"path": str(artifact / filename), "sha256": digest, "size": len(body)},
        **({"mcp_servers": payload["mcp_servers"]} if "mcp_servers" in payload else {}),
    }


def unpack_task(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Verify task identity, location, size and digest before parsing shared data."""
    if "task_payload_file" not in payload:
        return payload
    try:
        if set(payload) - {"task_id", "workdir", "artifact_workdir", "task_payload_file", "mcp_servers"}:
            raise ValueError("envelope")
        task_id, workdir, artifact = (str(payload[k]) for k in ("task_id", "workdir", "artifact_workdir"))
        ref = payload["task_payload_file"]
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "size"}:
            raise ValueError("reference")
        digest, size = ref["sha256"], ref["size"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("digest")
        if type(size) is not int or not 0 < size <= MAX_TASK_BYTES:
            raise ValueError("size")
        directory = Path(workdir) / ".igorek-artifacts" / task_id[:8]
        expected = directory / _filename(task_id, digest)
        if Path(artifact) != directory or ref["path"] != str(expected):
            raise ValueError("path")
        if not directory.resolve().is_relative_to(Path(workdir).resolve()):
            raise ValueError("symlink")
        fd = os.open(expected, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                raise ValueError("size")
            body = handle.read(size + 1)
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("integrity")
        full = json.loads(body)
        if not isinstance(full, dict) or "task_payload_file" in full:
            raise ValueError("content")
        if any(full.get(k) != payload[k] for k in ("task_id", "workdir", "artifact_workdir")):
            raise ValueError("identity")
        if "mcp_servers" in full:
            raise ValueError("persisted_credentials")
        if "mcp_servers" in payload:
            full["mcp_servers"] = payload["mcp_servers"]
        _validate(full)
        return full
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ChannelError("task_payload_file_invalid", type(exc).__name__) from exc
