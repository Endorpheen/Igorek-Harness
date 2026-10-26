"""Shell command helpers (port of codex-rs/tui/src/exec_command.rs).

In the Python port we use the standard library ``shlex`` for
round-trippable shell tokenization.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List, Optional, Sequence


def escape_command(command: Sequence[str]) -> str:
    """Quote-join ``command`` into a single shell-safe string.

    Port of ``codex::exec_command::escape_command``.

    Falls back to space-joining if ``shlex.join`` fails.
    """
    try:
        return shlex.join(list(command))
    except Exception:
        return " ".join(command)


_BASH_LC_RE = re.compile(r"^(?:/.*/)?(?:ba|z)sh$")


def strip_bash_lc_and_escape(command: Sequence[str]) -> str:
    """If ``command`` is ``bash -lc <script>`` (or zsh), return just ``<script>``.

    Port of ``codex::exec_command::strip_bash_lc_and_escape``.
    """
    parts = list(command)
    if len(parts) >= 3 and _BASH_LC_RE.match(parts[0]) and parts[1] in ("-lc", "-cl"):
        return parts[2]
    return escape_command(parts)


def split_command_string(command: str) -> List[str]:
    """Split ``command`` into tokens, preserving non-round-trippable input as-is.

    Port of ``codex::exec_command::split_command_string``.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return [command]
    if not parts:
        return [command]
    try:
        round_trip = shlex.join(parts)
    except Exception:
        return [command]
    if round_trip == command:
        return parts
    if ":\\" not in command:
        if shlex.split(round_trip) == parts:
            return parts
    return [command]


def relativize_to_home(path: Path) -> Optional[Path]:
    """If ``path`` is absolute and inside ``$HOME``, return the path relative to home.

    Port of ``codex::exec_command::relativize_to_home``.
    """
    if not path.is_absolute():
        return None
    home = Path(os.path.expanduser("~"))
    try:
        return path.relative_to(home)
    except ValueError:
        return None


__all__ = [
    "escape_command",
    "relativize_to_home",
    "split_command_string",
    "strip_bash_lc_and_escape",
]
