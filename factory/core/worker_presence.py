"""Advertise live queue consumers without changing their execution policy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .platform_support import LockBusyError, exclusive_lock
from .runtime import get_data_root


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Invalid CLI runtime directory")
    info = path.stat()
    if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("CLI runtime directory must be private to its owner")
    return path


def profile_directory(profile: str) -> Path:
    identity = hashlib.sha256(profile.encode()).hexdigest()[:24]
    return private_directory(private_directory(get_data_root() / "run" / "cli") / identity)


def signature(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def write_private(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def locked(path: Path) -> bool:
    try:
        descriptor = exclusive_lock(path)
    except LockBusyError:
        return True
    except FileNotFoundError:
        return False
    else:
        os.close(descriptor)
        return False


def consumers(profile: str, config: dict) -> bool:
    directory = private_directory(profile_directory(profile) / "workers")
    found = False
    for path in directory.glob("*.json"):
        if not locked(path.with_suffix(".lock")):
            continue
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            continue
        if value.get("signature") != signature(config):
            raise RuntimeError("A running worker uses a different configuration for this profile")
        if not value.get("ready", True):
            continue
        found = True
    return found


class WorkerPresence:
    def __init__(self, profile: str, config: dict, *, ready=True):
        directory = private_directory(profile_directory(profile) / "workers")
        self.path = directory / (uuid.uuid4().hex + ".json")
        self.descriptor = exclusive_lock(self.path.with_suffix(".lock"))
        self.value = {"signature": signature(config), "ready": ready}
        try:
            write_private(self.path, self.value)
        except BaseException:
            os.close(self.descriptor)
            raise

    def __enter__(self):
        return self

    def mark_ready(self):
        self.value["ready"] = True
        write_private(self.path, self.value)

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)
        os.close(self.descriptor)
        self.path.with_suffix(".lock").unlink(missing_ok=True)
