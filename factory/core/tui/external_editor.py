"""External editor launcher (port of codex-rs/tui/src/external_editor.rs).

The Python port uses the standard library only (``shlex`` for parsing and
``subprocess`` for execution). Tests are intentionally not ported per the
project's "no tests" policy.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence


class EditorError(Enum):
    MISSING_EDITOR = "neither VISUAL nor EDITOR is set"
    PARSE_FAILED = "failed to parse editor command"
    EMPTY_COMMAND = "editor command is empty"


class EditorLaunchError(Exception):
    def __init__(self, kind: EditorError) -> None:
        super().__init__(kind.value)
        self.kind = kind


def _resolve_windows_program(program: str) -> str:
    """Windows-only: respect PATHEXT for ``code.cmd`` shims (port one-to-one)."""
    found = shutil.which(program)
    return found if found else program


def resolve_editor_command(env: Optional[os._Environ] = None) -> List[str]:
    """Resolve the editor command from ``$VISUAL`` / ``$EDITOR`` (port one-to-one).

    Prefers ``VISUAL`` over ``EDITOR``.
    """
    e = env if env is not None else os.environ
    raw = e.get("VISUAL")
    if raw is None:
        raw = e.get("EDITOR")
    if raw is None:
        raise EditorLaunchError(EditorError.MISSING_EDITOR)
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        raise EditorLaunchError(EditorError.PARSE_FAILED) from exc
    if not parts:
        raise EditorLaunchError(EditorError.EMPTY_COMMAND)
    return parts


def _create_tempfile(suffix: str = ".md") -> str:
    """Return the path of a brand-new tempfile, releasing the handle (port one-to-one)."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def run_editor(seed: str, editor_cmd: Sequence[str]) -> str:
    """Write ``seed`` to a tempfile, launch the editor, return the updated content.

    Port of ``codex::external_editor::run_editor``.

    The function is synchronous in the Python port (no ``async``).
    """
    if not editor_cmd:
        raise EditorLaunchError(EditorError.EMPTY_COMMAND)

    temp_path = _create_tempfile(suffix=".md")
    try:
        Path(temp_path).write_text(seed, encoding="utf-8")

        program = _resolve_windows_program(editor_cmd[0]) if os.name == "nt" else editor_cmd[0]
        args = list(editor_cmd[1:]) + [temp_path]

        result = subprocess.run(
            [program, *args],
            stdin=None,
            stdout=None,
            stderr=None,
            check=False,
        )
        if result.returncode != 0:
            raise EditorLaunchError(EditorError.EMPTY_COMMAND)
        return Path(temp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


__all__ = [
    "EditorError",
    "EditorLaunchError",
    "resolve_editor_command",
    "run_editor",
]
