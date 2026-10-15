"""Check Bash write targets in workspace and user-read filesystem modes.

Running `cc_backend_tools._tool_bash` with `shell=True` and `cwd=workdir`
does not itself confine paths. File tools use `_validate_write_path` and
`_validate_read_path`; shell commands also need a write-target check for
`cp`, `mv`, `tee`, `install`, `sed -i`, `python3 -c`, `bash -c`, `find -exec`
and `>` redirection.

This module parses the command, finds write targets and rejects targets outside
the permitted roots: the working directory, configured Obsidian vault and extra
roots.

Limits:
  * This is a static heuristic, not a filesystem sandbox. Obfuscation such as
    `p=/repo; cp x $p`, base64|sh or code read from environment variables is
    outside its analysis. Use `wrap_with_sandbox()` (bwrap) for an OS boundary.
  * It does not restrict reads outside the working directory. Its purpose is
    to block writes or commands that perform those writes.

Modes (`IGOREK_BASH_SCOPE_GUARD`):
  * Unset: trial "warn" before `TRIAL_FLIP_DATE`, then forced "on". Missing or
    invalid dates also select "on" (fail closed).
  * "on": reject writes outside allowed roots.
  * "off"/"0"/"false"/"no"/"disabled": disable this check for debugging.
  * "warn"/"audit": warn without blocking.
  * Any other explicit value selects "on".
The first implicit trial warning is printed to stderr once. Warnings and denials
are recorded in `~/.cache/igorek-bash-scope/denials.jsonl` with heuristic secret
redaction. A logging failure does not change the decision.

The sandbox (`IGOREK_BASH_SANDBOX`, on by default; alternatives `off`/`observe`)
uses bubblewrap through `wrap_with_sandbox_argv`: the root is read-only, with
writes allowed in workdir, the configured Obsidian vault,
`IGOREK_BASH_WRITE_ROOTS`/`SANDBOX_RW` and the tool-reinstallation roots below.
This filesystem boundary is separate from the static command analysis.

Reinstalling the agent package (`uv tool install --force --reinstall .`) writes
to three uv locations: cache (`UV_CACHE_DIR`, default `~/.cache/uv`), tools
(`UV_TOOL_DIR`, default `~/.local/share/uv/tools`) and command shims
(`UV_TOOL_BIN_DIR`, default `~/.local/bin`). `tool_reinstall_roots` adds these
write roots so reinstalling the package does not require writable access to all
of `$HOME`. `IGOREK_BASH_TOOL_ROOTS` can override or disable the list (`off` adds
nothing; otherwise supply paths separated by `os.pathsep`).

In KVM (`IGOREK_ISOLATION_MODE=kvm`), these roots resolve from the guest's
environment and home. A guest reinstall changes only the guest environment;
host uv roots are not exported. Host updates use the typed `runtime.update`
broker operation in factory/core/isolation/self_update.py.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Sequence

__all__ = [
    "WriteTarget",
    "ScopeViolation",
    "allowed_write_roots",
    "tool_reinstall_roots",
    "split_segments",
    "collect_write_targets",
    "validate_bash_scope",
    "enforce_bash_scope",
    "bash_requires_approval",
    "wrap_with_sandbox",
    "wrap_with_sandbox_argv",
    "probe_bwrap_capability",
    "resolve_sandbox_mode",
    "sandbox_denied_message",
    "SANDBOX_DENIED_PREFIX",
    "GUARD_ENV",
    "SANDBOX_ENV",
    "SANDBOX_RW_ENV",
    "TOOL_ROOTS_ENV",
]

GUARD_ENV = "IGOREK_BASH_SCOPE_GUARD"
SANDBOX_ENV = "IGOREK_BASH_SANDBOX"
SANDBOX_RW_ENV = "IGOREK_BASH_SANDBOX_RW"
EXTRA_WRITE_ROOTS_ENV = "IGOREK_BASH_WRITE_ROOTS"
VAULT_ENV = "OBSIDIAN_VAULT_PATH"
# UV directories where `uv tool install --force --reinstall .` writes.
# Setting `off` (and aliases) disables their inclusion in allowed write roots;
# any other non-empty path list via `os.pathsep` overrides defaults.
TOOL_ROOTS_ENV = "IGOREK_BASH_TOOL_ROOTS"
TOOL_ROOTS_OFF_VALUES = frozenset({"off", "0", "no", "false", "disabled"})

# The global sandbox (`bwrap`) does not depend on security levels—it either is enabled
# (via `env` + DB configuration) and defaults to fail-closed, or is explicitly disabled.
SANDBOX_DENIED_PREFIX = "SANDBOX_UNAVAILABLE"
SANDBOX_OFF_VALUES = frozenset({"off", "0", "no", "disabled"})
SANDBOX_OBSERVE_VALUE = "observe"
_SANDBOX_PROBE_TIMEOUT_S = 3.0
_NAMESPACE_BLOCKED_MARKERS = (
    "Operation not permitted",
    "Failed to create new namespace",
    "setting up uid map",
    "Creating new namespace failed",
)

VERDICT_ALLOW = "allow"
VERDICT_DENY = "deny"
VERDICT_WARN = "warn"

# Trial mode: without explicit `env`, "warn" applies until `TRIAL_FLIP_DATE`;
# after the date, it enforces "on" (fail-closed). Date in ISO (UTC) format.
TRIAL_DEFAULT_MODE = "warn"
TRIAL_FLIP_DATE = "2026-10-21"

# Commands where output is written to EVERY path argument.
WRITE_ALL_ARGS = frozenset(
    {
        "rm",
        "rmdir",
        "mkdir",
        "touch",
        "chmod",
        "chown",
        "chgrp",
        "truncate",
        "tee",
        "mkfifo",
        "mknod",
        "shred",
        "unlink",
    }
)

# Commands where output is written to the LAST path argument (destination).
WRITE_LAST_ARG = frozenset({"cp", "mv", "install", "rsync", "ln", "scp", "sftp", "cpio"})

SHELL_COMMANDS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "fish"})
SCRIPT_RUNNERS = frozenset({"node", "nodejs", "perl", "ruby", "php", "lua", "deno", "bun", "osascript"})
PY_RUNNER_RE = re.compile(r"^(?:python|pypy)[0-9.]*$")

PREFIX_COMMANDS = frozenset(
    {"sudo", "doas", "command", "nohup", "setsid", "time", "nice", "ionice", "stdbuf", "env", "timeout"}
)

OPERATOR_TOKENS = frozenset({"|", "||", "&&", ";", "&", "(", ")"})
# The `<>` operator opens a file for read-write (creates it), so this is the target for writing.
REDIRECT_OUT_TOKENS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})
REDIRECT_IN_TOKENS = frozenset({"<", "<<", "<<<", "<&"})
# `>&` — redirects file descriptor (`2>&1`), not file writing; treated as
# simply skipping the next token; in `_add_redirect_targets`, it does NOT become a target.
REDIRECT_SKIP_ONLY_TOKENS = frozenset({">&"})
ALL_REDIRECT_TOKENS = REDIRECT_OUT_TOKENS | REDIRECT_IN_TOKENS | REDIRECT_SKIP_ONLY_TOKENS

SAFE_DEVICE_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero", "/dev/full"})
ABS_PATH_RE = re.compile(r"(?:^|[\s'\"(=,\[])(/[A-Za-z0-9._@%+,\-][^\s'\"`,;)\]}<>|&]*)")


@dataclass(frozen=True)
class WriteTarget:
    """Determines where the command writes and the reasoning behind this decision."""

    path: str
    kind: str  # redirect | mutate | exec_abs_path | interpreter_script | unresolved
    source: str


class ScopeViolation(Exception):
    """Purpose of writing outside permitted directories."""

    def __init__(self, target: WriteTarget, roots: Sequence[str]) -> None:
        self.target = target
        self.roots = list(roots)
        super().__init__(f"{target.kind}:{target.path}")


# --------------------------- --------------------------- ------------ #
# ROOTS
# --------------------------- --------------------------- ------------ #


def _uv_tool_dirs(env: dict[str, str]) -> list[str]:
    """UV directories follow his own rules: env → XDG → default home directories."""
    home = str(env.get("HOME") or "").strip() or os.path.expanduser("~")
    cache_home = str(env.get("XDG_CACHE_HOME") or "").strip() or os.path.join(home, ".cache")
    data_home = str(env.get("XDG_DATA_HOME") or "").strip() or os.path.join(
        home, ".local", "share"
    )
    cache_dir = str(env.get("UV_CACHE_DIR") or "").strip() or os.path.join(cache_home, "uv")
    tool_dir = str(env.get("UV_TOOL_DIR") or "").strip() or os.path.join(
        data_home, "uv", "tools"
    )
    bin_dir = str(env.get("UV_TOOL_BIN_DIR") or "").strip() or os.path.join(
        home, ".local", "bin"
    )
    return [cache_dir, tool_dir, bin_dir]


def tool_reinstall_roots(env: dict[str, str] | None = None) -> list[str]:
    """Directories where `uv tool install --force --reinstall .` writes output.

    Required for the agent to reinstall its own tool package:
      * UV cache — lock files, temporary files, unpacked wheels, and builds;
      * tools root directory — `.lock` file and the package's own environment directory;
      * shim scripts directory — entrypoints are recreated via `unlink` + `symlink`.

    `IGOREK_BASH_TOOL_ROOTS=off` (or 0/no/false/disabled) — exclude them;
    otherwise, a non-empty value is interpreted as a path list separated by `os.pathsep`.
    """
    env = env if env is not None else os.environ
    raw = str(env.get(TOOL_ROOTS_ENV) or "").strip()
    if raw.lower() in TOOL_ROOTS_OFF_VALUES:
        return []
    if raw:
        return [item.strip() for item in raw.split(os.pathsep) if item.strip()]
    return _uv_tool_dirs(env)


def allowed_write_roots(
    workdir: str,
    *,
    extra_roots: Iterable[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """List of allowed root directories (resolved with `realpath`, duplicates removed)."""
    env = env if env is not None else os.environ
    roots: list[str] = []

    def _add(path: str | None) -> None:
        if not path:
            return
        candidate = os.path.realpath(os.path.expanduser(str(path).strip()))
        if candidate and candidate != "/" and candidate not in roots:
            roots.append(candidate)

    _add(workdir)
    for item in tool_reinstall_roots(env):
        _add(item)
    # Guest Bash runs in a nested bwrap namespace with /tmp freshly mounted as
    # tmpfs. It is disposable guest scratch, not a host path. Keep this
    # conditional on the guest marker so host Bash gains no new write root.
    try:
        from .isolation.guest_bwrap import guest_mode_requested

        if guest_mode_requested(env):
            _add("/tmp")
    except ImportError:  # pragma: no cover - direct-script compatibility
        pass
    _add(env.get(VAULT_ENV))
    for item in (env.get(EXTRA_WRITE_ROOTS_ENV) or "").split(os.pathsep):
        _add(item)
    for item in extra_roots or ():
        _add(item)
    return roots


def _is_inside(path: str, roots: Sequence[str]) -> bool:
    resolved = os.path.realpath(os.path.expanduser(path))
    if resolved in SAFE_DEVICE_TARGETS:
        return True
    for root in roots:
        if resolved == root or resolved.startswith(root.rstrip("/") + os.sep):
            return True
    return False


def _is_safe_device(path: str) -> bool:
    resolved = os.path.realpath(os.path.expanduser(path))
    if resolved in SAFE_DEVICE_TARGETS:
        return True
    return bool(re.match(r"^/dev/(?:fd|stdin|stdout|stderr)/", resolved)) or bool(
        re.match(r"^/proc/(?:self|\d+)/fd/", resolved)
    )


# --------------------------- --------------------------- ------------ #
# Tokenization and Segmentation
# --------------------------- --------------------------- ------------ #


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;()<>")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:
        # Unclosed quote/escape: rough parsing is better than nothing.
        return command.split()


def split_segments(tokens: Sequence[str]) -> list[tuple[list[str], str, int]]:
    """Splits tokens into simple commands while preserving the separator and bracket depth.

    Returns a list of tuples ``(tokens, separator, depth)``:
      * ``separator`` — operator **AFTER** the segment (empty string for the last one);
      * ``depth`` — depth of round brackets at the **START** of the segment.
    """
    segments: list[tuple[list[str], str, int]] = []
    current: list[str] = []
    depth = 0
    seg_depth = 0
    for token in tokens:
        if token == "(":
            if current:
                segments.append((current, "(", seg_depth))
                current = []
            depth += 1
            seg_depth = depth
            continue
        if token == ")":
            if current:
                segments.append((current, ")", seg_depth))
                current = []
            depth = max(0, depth - 1)
            seg_depth = depth
            continue
        if token in OPERATOR_TOKENS:
            if current:
                segments.append((current, token, seg_depth))
                current = []
            seg_depth = depth
            continue
        if not current:
            seg_depth = depth
        current.append(token)
    if current:
        segments.append((current, "", seg_depth))
    return segments


def _looks_like_path(token: str) -> bool:
    if token in ALL_REDIRECT_TOKENS or token in OPERATOR_TOKENS:
        return False
    if token in {"{}", "\\;", "+"}:
        return False
    if token.startswith(("/", "~", "./", "../")):
        return True
    return "/" in token and not token.startswith("-")


def _strip(token: str) -> str:
    return token.strip().strip("'\"")


def _skip_prefixes(tokens: Sequence[str]) -> int:
    idx = 0
    while idx < len(tokens):
        token = os.path.basename(_strip(tokens[idx]))
        if token in PREFIX_COMMANDS:
            idx += 1
            while idx < len(tokens) and (
                tokens[idx].startswith("-")
                or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx])
                or re.match(r"^\d+[smhd]?$", tokens[idx])
            ):
                idx += 1
            continue
        break
    return idx


# ---------------------------- #
# Collection of write targets
# ---------------------------- #


_CD_OK_SEPARATORS = frozenset({"", ";", "&&", "||"})


def _make_target(
    raw: str,
    kind: str,
    source: str,
    cwd: str | None,
    cwd_known: bool,
) -> WriteTarget:
    """Resolves target from tracked working directory; relative paths remain unresolved if the working directory is unknown."""
    path = _strip(raw)
    if not path:
        return WriteTarget(path, kind, source)
    if path.startswith("~"):
        path = os.path.expanduser(path)
    if os.path.isabs(path):
        return WriteTarget(os.path.normpath(path), kind, source)
    if not cwd_known or not cwd:
        return WriteTarget(path, "unresolved", source)
    return WriteTarget(os.path.normpath(os.path.join(cwd, path)), kind, source)


def _apply_cd(
    tokens: Sequence[str],
    separator: str,
    depth: int,
    cwd: str | None,
    cwd_known: bool,
) -> tuple[str | None, bool]:
    """Update the tracked cwd for one segment; ambiguity sets cwd_known=False.

    `cd <static path>` changes cwd only at the top level with an empty separator,
    `;`, `&&` or `||`. `pushd`, `popd`, `cd -`, bare `cd`, `cd $VAR`, pipelines,
    background jobs and parenthesized commands make cwd unknown.
    """
    start = _skip_prefixes(tokens)
    if start >= len(tokens):
        return cwd, cwd_known
    head = os.path.basename(_strip(tokens[start]))
    if head in {"pushd", "popd"}:
        return cwd, False
    if head != "cd":
        return cwd, cwd_known
    if depth != 0 or separator not in _CD_OK_SEPARATORS:
        return cwd, False
    operands = [t for t in tokens[start + 1 :] if t and not t.startswith("-")]
    if not operands:
        return cwd, False
    target = _strip(operands[0])
    if not target or "$" in target or "`" in target:
        return cwd, False
    if target.startswith("~"):
        target = os.path.expanduser(target)
    if not os.path.isabs(target):
        if not cwd_known or not cwd:
            return cwd, False
        target = os.path.join(cwd, target)
    return os.path.normpath(target), True


def _collect_command(
    command: str,
    out: list[WriteTarget],
    depth: int,
    cwd: str | None,
    cwd_known: bool,
) -> None:
    """Sequential traversal of segments with tracking of the working directory and bracket depth."""
    segments = split_segments(_tokenize(command))
    saved_outer: tuple[str | None, bool] | None = None
    for tokens, separator, seg_depth in segments:
        if seg_depth > 0 and saved_outer is None:
            saved_outer = (cwd, cwd_known)
        elif seg_depth == 0 and saved_outer is not None:
            cwd, cwd_known = saved_outer
            saved_outer = None
        cwd, cwd_known = _apply_cd(tokens, separator, seg_depth, cwd, cwd_known)
        _collect_segment(tokens, out, depth, cwd, cwd_known)


def _add_redirect_targets(
    args: Sequence[str],
    out: list[WriteTarget],
    cwd: str | None,
    cwd_known: bool,
) -> None:
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in REDIRECT_OUT_TOKENS:
            if idx + 1 < len(args):
                out.append(_make_target(args[idx + 1], "redirect", token, cwd, cwd_known))
            idx += 2
            continue
        if token in REDIRECT_SKIP_ONLY_TOKENS:
            # `>&word` — only works if `word` is numeric or `-`
            # (`2>&1`, `2>&-`); otherwise, Bash interprets it as `&>word`, meaning file redirection (`echo pwn >& /FOREIGN/x`).
            nxt = args[idx + 1] if idx + 1 < len(args) else None
            if nxt is not None and not nxt.isdigit() and nxt != "-":
                out.append(_make_target(nxt, "redirect", token, cwd, cwd_known))
            idx += 2
            continue
        idx += 1


def _path_args(args: Sequence[str]) -> list[str]:
    """All non-empty operands that do not start with a '-'; including bare names without a '/'."""
    result: list[str] = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in ALL_REDIRECT_TOKENS:
            idx += 2
            continue
        if token.isdigit() and idx + 1 < len(args) and args[idx + 1] in ALL_REDIRECT_TOKENS:
            # `2>/dev/null`, `2>&1`: The number is a *file descriptor* for redirection, not an operand.
            idx += 1
            continue
        if token and not token.startswith("-"):
            result.append(_strip(token))
        idx += 1
    return result


_SED_SCRIPT_FLAGS = frozenset({"-e", "--expression", "-f", "--file"})
_SED_SCRIPT_PREFIXES = ("--expression=", "--file=")
_PERL_INLINE_FLAGS = frozenset({"-e", "-E"})
_PERL_BUNDLE_PREFIX = "pnal"

_TOOL_OUTPUT_SPECS: dict[str, tuple[frozenset[str], tuple[str, ...], str]] = {
    "tar": (frozenset({"-C", "--directory"}), ("--directory=",), "tar -C"),
    "unzip": (frozenset({"-d"}), (), "unzip -d"),
    "curl": (frozenset({"-o", "--output"}), ("--output=",), "curl -o"),
    "wget": (frozenset({"-O", "--output-document"}), ("--output-document=",), "wget -O"),
}
_GIT_DIR_EXACT = frozenset({"--git-dir", "--work-tree"})
_GIT_DIR_PREFIXES = ("--git-dir=", "--work-tree=")


def _operands_without_inline_script(
    args: Sequence[str],
    inline_flags: frozenset[str],
    inline_prefixes: Sequence[str],
) -> list[str]:
    """Operands without script expressions (the first one if neither `-e` nor `-f` is specified, or after `-e`/`-f`)."""
    operands: list[str] = []
    explicit_script = False
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in ALL_REDIRECT_TOKENS:
            # `< /FOREIGN/input`, `> /dev/null`, `2>&1`, `<<< word` — not operands.
            idx += 2
            continue
        if token.isdigit() and idx + 1 < len(args) and args[idx + 1] in ALL_REDIRECT_TOKENS:
            # `2` before redirection — file descriptor number, not an operand.
            idx += 1
            continue
        if token in inline_flags:
            explicit_script = True
            idx += 2
            continue
        if any(token.startswith(prefix) for prefix in inline_prefixes):
            explicit_script = True
            idx += 1
            continue
        if token.startswith("-") and token != "-":
            idx += 1
            continue
        operands.append(_strip(token))
        idx += 1
    if not explicit_script and operands:
        operands = operands[1:]
    return operands


def _sed_inplace_requested(args: Sequence[str]) -> bool:
    for part in args:
        if part.startswith("-i"):
            return True
        if part == "--in-place" or part.startswith("--in-place="):
            return True
    return False


def _perl_inplace_requested(args: Sequence[str]) -> bool:
    for part in args:
        if not part.startswith("-") or part.startswith("--"):
            continue
        rest = part[1:]
        while rest and rest[0] in _PERL_BUNDLE_PREFIX:
            rest = rest[1:]
        if rest.startswith("i"):
            return True
    return False


def _collect_tool_output(
    base: str,
    args: Sequence[str],
    out: list[WriteTarget],
    cwd: str | None,
    cwd_known: bool,
) -> None:
    exact, prefixes, source = _TOOL_OUTPUT_SPECS[base]
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token in exact:
            if idx + 1 < len(args):
                out.append(_make_target(args[idx + 1], "mutate", source, cwd, cwd_known))
            idx += 2
            continue
        matched = next((prefix for prefix in prefixes if token.startswith(prefix)), None)
        if matched is not None:
            out.append(_make_target(token[len(matched) :], "mutate", source, cwd, cwd_known))
        idx += 1


def _collect_git_dirs(
    args: Sequence[str],
    out: list[WriteTarget],
    cwd: str | None,
    cwd_known: bool,
) -> None:
    """Any `git` invocation with `-C`, `--git-dir`, or `--work-tree` outside the root directory is a write target.

    The subcommand does not matter (the allowlist of subcommands is incomplete), so we check the directory path itself rather than a list of "safe" subcommands.
    """
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "-C":
            if idx + 1 < len(args):
                out.append(_make_target(args[idx + 1], "mutate", "git -C", cwd, cwd_known))
            idx += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            out.append(_make_target(token[2:], "mutate", "git -C", cwd, cwd_known))
            idx += 1
            continue
        if token in _GIT_DIR_EXACT:
            if idx + 1 < len(args):
                out.append(
                    _make_target(args[idx + 1], "mutate", f"git {token}", cwd, cwd_known)
                )
            idx += 2
            continue
        matched = next((prefix for prefix in _GIT_DIR_PREFIXES if token.startswith(prefix)), None)
        if matched is not None:
            source = f"git {matched.rstrip('=')}"
            out.append(_make_target(token[len(matched) :], "mutate", source, cwd, cwd_known))
        idx += 1


def _collect_from_args(
    base: str,
    args: Sequence[str],
    out: list[WriteTarget],
    depth: int,
    cwd: str | None,
    cwd_known: bool,
) -> None:
    paths = _path_args(args)
    if base in {"source", "."}:
        for path in paths:
            out.append(_make_target(path, "interpreter_script", base, cwd, cwd_known))
        return

    if base in _TOOL_OUTPUT_SPECS:
        _collect_tool_output(base, args, out, cwd, cwd_known)
        return

    if base == "git":
        _collect_git_dirs(args, out, cwd, cwd_known)
        return

    if base in WRITE_ALL_ARGS:
        for path in paths:
            out.append(_make_target(path, "mutate", base, cwd, cwd_known))
        return

    if base in WRITE_LAST_ARG:
        # The `cp/mv/install` command with `-t DIR /` or `--target-directory[=]DIR` flag: the target is the destination directory.
        target_dir: str | None = None
        idx = 0
        while idx < len(args):
            token = args[idx]
            if token in {"-t", "--target-directory"}:
                if idx + 1 < len(args):
                    target_dir = args[idx + 1]
                idx += 2
                continue
            if token.startswith("--target-directory="):
                target_dir = token[len("--target-directory=") :]
                idx += 1
                continue
            if token.startswith("-t") and len(token) > 2 and not token.startswith("--"):
                target_dir = token[2:]
                idx += 1
                continue
            idx += 1
        if target_dir is not None:
            out.append(_make_target(target_dir, "mutate", f"{base} -t", cwd, cwd_known))
        elif paths:
            out.append(_make_target(paths[-1], "mutate", base, cwd, cwd_known))
        return

    if base == "sed":
        if _sed_inplace_requested(args):
            file_operands = _operands_without_inline_script(
                args, _SED_SCRIPT_FLAGS, _SED_SCRIPT_PREFIXES
            )
            for path in file_operands:
                out.append(_make_target(path, "mutate", "sed -i", cwd, cwd_known))
        return

    if base == "perl":
        if _perl_inplace_requested(args):
            file_operands = _operands_without_inline_script(args, _PERL_INLINE_FLAGS, ())
            for path in file_operands:
                out.append(_make_target(path, "mutate", "perl -i", cwd, cwd_known))
        return

    if base == "sort":
        for idx, token in enumerate(args):
            if token == "-o" and idx + 1 < len(args):
                out.append(_make_target(args[idx + 1], "mutate", "sort -o", cwd, cwd_known))
        return

    if base == "dd":
        for token in args:
            if token.startswith("of="):
                out.append(_make_target(token[3:], "mutate", "dd of=", cwd, cwd_known))
        return

    if base == "xargs":
        for idx, token in enumerate(args):
            if token.startswith("-"):
                continue
            inner_base = os.path.basename(_strip(token))
            if inner_base in WRITE_ALL_ARGS or inner_base in WRITE_LAST_ARG or inner_base in SHELL_COMMANDS:
                _collect_from_args(inner_base, args[idx + 1 :], out, depth, cwd, cwd_known)
                break
        return

    if PY_RUNNER_RE.match(base) or base in SCRIPT_RUNNERS:
        _collect_interpreter(base, args, out, depth, cwd, cwd_known)
        return

    if base in SHELL_COMMANDS and depth < 3:
        _collect_shell(base, args, out, depth, cwd, cwd_known)
        return


def _collect_interpreter(
    base: str,
    args: Sequence[str],
    out: list[WriteTarget],
    depth: int,
    cwd: str | None,
    cwd_known: bool,
) -> None:
    inline = None
    for idx, token in enumerate(args):
        if token in {"-c", "-e", "--eval"} and idx + 1 < len(args):
            inline = args[idx + 1]
            break
    if inline is None and depth < 3:
        # Run `python3 script.py`: executes the script file along with its arguments.
        for idx, token in enumerate(args):
            if token.startswith("-") or token == "":
                continue
            script = _strip(token)
            if _looks_like_path(script):
                out.append(_make_target(script, "interpreter_script", base, cwd, cwd_known))
            break
        blob = " ".join(args)
    else:
        blob = inline or ""

    # Any absolute path within executable code is considered a potential target for writing; static code content cannot be verified.
    for match in ABS_PATH_RE.findall(blob):
        candidate = _strip(match)
        if candidate and candidate.startswith("/") and len(candidate) > 1:
            out.append(_make_target(candidate, "exec_abs_path", base, cwd, cwd_known))
    # String paths like `Path("/x")` and single quotes are already handled above.
    for match in re.findall(r"['\"](/[^'\"]+)['\"]", blob):
        out.append(_make_target(match, "exec_abs_path", base, cwd, cwd_known))


def _collect_shell(
    base: str,
    args: Sequence[str],
    out: list[WriteTarget],
    depth: int,
    cwd: str | None,
    cwd_known: bool,
) -> None:
    for idx, token in enumerate(args):
        if token == "-c" and idx + 1 < len(args):
            inner = args[idx + 1]
            # The current working directory (cwd) inherits from the external context and is then tracked internally.
            _collect_command(inner, out, depth + 1, cwd, cwd_known)
            return
    for token in args:
        if token.startswith("-"):
            continue
        script = _strip(token)
        if _looks_like_path(script):
            out.append(_make_target(script, "interpreter_script", base, cwd, cwd_known))
        return


def _collect_segment(
    tokens: Sequence[str],
    out: list[WriteTarget],
    depth: int = 0,
    cwd: str | None = None,
    cwd_known: bool = False,
) -> None:
    if not tokens:
        return
    args_view = list(tokens)
    _add_redirect_targets(args_view, out, cwd, cwd_known)

    # `find ... -exec CMD ... \;` (including `-execdir`, `-ok`, `-okdir`; all occurrences)
    idx = 0
    while idx < len(args_view):
        if args_view[idx] not in {"-exec", "-execdir", "-ok", "-okdir"}:
            idx += 1
            continue
        end = idx + 1
        while end < len(args_view) and args_view[end] not in {";", "+"}:
            end += 1
        inner = list(args_view[idx + 1 : end])
        if inner:
            inner_base = os.path.basename(_strip(inner[0]))
            _collect_from_args(inner_base, inner[1:], out, depth + 1, cwd, cwd_known)
        idx = end + 1

    start = _skip_prefixes(args_view)
    if start >= len(args_view):
        return
    head = _strip(args_view[start])
    base = os.path.basename(head) if head else ""
    if not base:
        return
    _collect_from_args(base, args_view[start + 1 :], out, depth, cwd, cwd_known)


def collect_write_targets(command: str, *, cwd: str | None = None) -> list[WriteTarget]:
    """All write targets recognized in the command, resolved from the tracked working directory (`cwd`).

    The initial `cwd` is ``cwd``; relative targets resolve from it, and if the `cwd` is unknown, they receive `kind="unresolved"`.
    """
    targets: list[WriteTarget] = []
    cwd_value = os.path.normpath(cwd) if cwd else None
    _collect_command(command, targets, 0, cwd_value, cwd_value is not None)
    deduped: list[WriteTarget] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        key = (target.kind, target.path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_bash_scope(
    command: str,
    *,
    workdir: str,
    extra_roots: Iterable[str] | None = None,
) -> ScopeViolation | None:
    """Return the first write-area violation or `None`."""
    if not command or not str(command).strip():
        return None
    roots = allowed_write_roots(workdir, extra_roots=extra_roots)
    for target in collect_write_targets(command, cwd=workdir):
        path = target.path
        if not path or path in {"-"}:
            continue
        if target.kind == "unresolved":
            return ScopeViolation(target, roots)
        if _is_safe_device(path):
            continue
        if "$" in path or "`" in path:
            return ScopeViolation(WriteTarget(path, "unresolved", target.source), roots)
        if _is_inside(path, roots):
            continue
        return ScopeViolation(target, roots)
    return None


def _today() -> str:
    """Current date in ISO (UTC) format. A separate function to allow tests to override."""
    return datetime.now(timezone.utc).date().isoformat()


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _trial_default_mode() -> str:
    """Issue a warning before `TRIAL_FLIP_DATE`; after it, switch to `on`. If the date is invalid or missing, default to `on`."""
    try:
        flip = _parse_date(TRIAL_FLIP_DATE)
        if flip is None:
            return "on"
        today = _parse_date(_today())
        if today is None:
            return "on"
        return "on" if today >= flip else TRIAL_DEFAULT_MODE
    except Exception:
        return "on"


def _guard_env_explicit(env: dict[str, str] | None = None) -> bool:
    try:
        env = env if env is not None else os.environ
        raw = env.get(GUARD_ENV)
        return raw is not None and str(raw).strip() != ""
    except Exception:
        return False


# Explicit `guard_mode` (argument) and explicit `env` variables recognize the same aliases.
# Difference: when `env` has an unknown value, it is treated as "on" (fail-closed, `_guard_mode`),
# whereas the `guard_mode` argument returns the unknown value back to `env` (None).
_GUARD_MODE_ALIASES: dict[str, str] = {
    "off": "off",
    "0": "off",
    "false": "off",
    "no": "off",
    "disabled": "off",
    "warn": "warn",
    "audit": "warn",
    "on": "on",
    "1": "on",
    "true": "on",
    "yes": "on",
    "enabled": "on",
}


def _normalize_guard_mode(guard_mode: object) -> str | None:
    """Explicit `guard_mode` setting: `off`, `warn`, or `on`; if unspecified or unknown, defaults to `None` (meaning environment variable)."""
    if guard_mode is None:
        return None
    text = str(guard_mode).strip().lower()
    if not text:
        return None
    return _GUARD_MODE_ALIASES.get(text)


def _guard_mode(env: dict[str, str] | None = None) -> str:
    """Guard mode setting; **must never throw an exception** (otherwise, fallback to `fail-open`).

    Explicit environment variable (`env`) takes precedence. Without `env`, use a **trial warning** with mandatory transition to `on` after `TRIAL_FLIP_DATE`. Any error or invalid date defaults to `on`.
    """
    try:
        env = env if env is not None else os.environ
        raw = env.get(GUARD_ENV)
        if raw is not None and str(raw).strip():
            value = str(raw).strip().lower()
            if value in {"off", "0", "false", "no", "disabled"}:
                return "off"
            if value in {"warn", "audit"}:
                return "warn"
            return "on"
        return _trial_default_mode()
    except Exception:
        return "on"


_WARN_NOTICE_EMITTED = False


def _maybe_emit_trial_warning() -> None:
    """A **loud one-time warning** is issued on the first `warn` without an explicit environment variable."""
    global _WARN_NOTICE_EMITTED
    if _WARN_NOTICE_EMITTED or _guard_env_explicit():
        return
    _WARN_NOTICE_EMITTED = True
    try:
        print(
            "[bash_scope_guard] ВНИМАНИЕ: испытательный режим warn — запись вне "
            "workdir НЕ блокируется. С " + str(TRIAL_FLIP_DATE) + " режим сам станет "
            "fail-closed 'on'. Задай IGOREK_BASH_SCOPE_GUARD=on/off/warn явно, "
            "чтобы убрать это предупреждение.",
            file=sys.stderr,
        )
    except Exception:
        return


def enforce_bash_scope(
    command: str,
    guard_mode: str | None = None,
    *,
    workdir: str,
    filesystem_mode: str = "workspace",
    extra_roots: Iterable[str] | None = None,
) -> tuple[str, str]:
    """Command verdict resolution: `(verdict, text)`. `verdict` ∈ `allow|deny|warn`.

    `guard_mode` is an explicit mode (on/off/warn, aliases as in env). Explicit values override env; `None`/`empty`/`unknown` values default to env mode (``_guard_mode()``, including a trial `warn` flip).
    """
    mode = str(filesystem_mode or "workspace").strip().lower()
    if mode == "full-dangerous":
        return VERDICT_ALLOW, ""
    explicit_mode = _normalize_guard_mode(guard_mode)
    guard = explicit_mode if explicit_mode is not None else _guard_mode()
    if guard == "off":
        return VERDICT_ALLOW, ""
    violation = validate_bash_scope(command, workdir=workdir, extra_roots=extra_roots)
    if violation is None:
        return VERDICT_ALLOW, ""
    roots = ", ".join(violation.roots) or "(нет)"
    text = (
        "POLICY_DENIED: bash_write_outside_workdir\n"
        f"  цель записи : {violation.target.path}\n"
        f"  как понято  : {violation.target.kind} ({violation.target.source})\n"
        f"  рабочий корень: {roots}\n"
        "  filesystem_mode=workspace разрешает запись только внутри перечисленных "
        "корней (рабочий каталог, каталоги uv для переустановки собственного "
        "пакета, Obsidian vault, IGOREK_BASH_WRITE_ROOTS).\n"
        "  Если команда нужна именно такая — либо приведи путь внутрь workdir, либо "
        "попроси пользователя выполнить её вручную (или профиль full-dangerous)."
    )
    if guard == "warn":
        if explicit_mode is None:
            _maybe_emit_trial_warning()
        _record_telemetry(VERDICT_WARN, command, violation, workdir, guard)
        return VERDICT_WARN, text
    _record_telemetry(VERDICT_DENY, command, violation, workdir, guard)
    return VERDICT_DENY, text


# --------------------------------------------------------------------------- #
# Telemetry (JSONL, best-effort: errors do not affect decision-making)
# --------------------------------------------------------------------------- #

_TELEMETRY_DIR = os.path.join(os.path.expanduser("~"), ".cache", "igorek-bash-scope")
_TELEMETRY_FILE = "denials.jsonl"
_TELEMETRY_MAX_BYTES = 5 * 1024 * 1024

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer\s+)?)\S+"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1***"),
    (re.compile(r"(--(?:password|passwd|token|secret)=)\S+"), r"\1***"),
    (re.compile(r"(--(?:password|passwd|token|secret)\s+)\S+"), r"\1***"),
    (re.compile(r"(?<!\S)([A-Za-z_][A-Za-z0-9_]*)=\S+"), r"\1=***"),
    (re.compile(r"(?<!\S)-p\s+\S+"), "-p ***"),
    (re.compile(r"(?<!\S)-u\s+\S+"), "-u ***"),
)


def _redact_command(command: str) -> str:
    text = str(command)
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _telemetry_path() -> str:
    return os.path.join(_TELEMETRY_DIR, _TELEMETRY_FILE)


def _rotate_telemetry_if_needed(path: str) -> None:
    try:
        if os.path.getsize(path) <= _TELEMETRY_MAX_BYTES:
            return
    except OSError:
        return
    backup = path + ".1"
    # Race condition: concurrent processes may rotate simultaneously and lose
    # some old strings (telemetry best-effort). No `.2..N` files are created.
    os.replace(path, backup)
    try:
        os.chmod(backup, 0o600)
    except OSError:
        return


def _write_telemetry(path: str, record: dict[str, object]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    _rotate_telemetry_if_needed(path)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _record_telemetry(
    verdict: str,
    command: str,
    violation: ScopeViolation | None,
    workdir: str,
    mode: str,
) -> None:
    try:
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "mode": mode,
            "workdir": os.path.realpath(workdir) if workdir else "",
            "target": violation.target.path if violation else "",
            "kind": violation.target.kind if violation else "",
            "source": violation.target.source if violation else "",
            # Pre-cutting edit: secrets must not survive truncation.
            "command": _redact_command(command)[:200],
        }
        _write_telemetry(_telemetry_path(), record)
    except Exception:
        return


def bash_requires_approval(command: str, *, workdir: str | None = None) -> bool:
    """Should confirmation be requested: command may write to filesystem (FS)."""
    if not command:
        return False
    tokens = _tokenize(command)
    mutating = WRITE_ALL_ARGS | WRITE_LAST_ARG | {"sed", "dd", "perl"} | SHELL_COMMANDS | SCRIPT_RUNNERS
    for segment, _separator, _depth in split_segments(tokens):
        start = _skip_prefixes(segment)
        if start >= len(segment):
            continue
        base = os.path.basename(_strip(segment[start]))
        if base in mutating or PY_RUNNER_RE.match(base):
            return True
        if any(token in REDIRECT_OUT_TOKENS for token in segment):
            if any(t not in SAFE_DEVICE_TARGETS for t in segment):
                return True
    return False


# --------------------------------------------------------------------------- #
# Level 2: True filesystem isolation via bubblewrap
# --------------------------------------------------------------------------- #


def resolve_sandbox_mode(env: dict[str, str] | None = None) -> str:
    """Environment-based sandbox mode: ``off`` | ``observe`` | ``on`` (default: ``on``)."""
    try:
        env = env if env is not None else os.environ
        raw = env.get(SANDBOX_ENV)
    except Exception:
        return "on"
    if raw is None:
        return "on"
    value = str(raw).strip().lower()
    if not value:
        return "on"
    if value in SANDBOX_OFF_VALUES:
        return "off"
    if value == SANDBOX_OBSERVE_VALUE:
        return "observe"
    return "on"


@functools.lru_cache(maxsize=1)
def probe_bwrap_capability() -> str:
    """Verification that `bwrap` executes successfully: ``ok``/``bwrap_missing``/``namespace_blocked``.

    Inside a guest (`IGOREK_ISOLATION_GUEST`), the probe executes the hardened
    ``--unshare-all``-class arguments, so the status reflects the exact invocation
    that will isolate Bash instead of a weaker host-style one. Fail-closed
    callers thus reject execution whenever the hardened probe fails.
    """
    from .isolation.guest_bwrap import guest_mode_requested, probe_guest_bwrap

    if guest_mode_requested():
        probe = probe_guest_bwrap()
        if probe.ok:
            return "ok"
        return (
            "bwrap_missing"
            if probe.reason == "bwrap_missing"
            else "namespace_blocked"
        )
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return "bwrap_missing"
    argv = [
        bwrap,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--unshare-pid",
        "/bin/true",
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_SANDBOX_PROBE_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return "bwrap_missing"
    except (subprocess.TimeoutExpired, OSError):
        return "namespace_blocked"
    if proc.returncode == 0:
        return "ok"
    output = (proc.stderr or "") + (proc.stdout or "")
    if any(marker in output for marker in _NAMESPACE_BLOCKED_MARKERS):
        return "namespace_blocked"
    return "namespace_blocked"


def sandbox_denied_message(reason: str) -> str:
    """Fail-closed message: Sandbox enabled, but execution is impossible."""
    return (
        f"{SANDBOX_DENIED_PREFIX}: {reason}. Bash is blocked because the sandbox "
        "cannot run; disable it explicitly in Settings → Sandbox "
        "(or set IGOREK_BASH_SANDBOX=off) to proceed."
    )


def _app_owned_write_roots(
    workdir: str,
    *,
    extra_roots: Iterable[str] | None,
    env: dict[str, str],
) -> set[str]:
    """Realpath set of allowed roots for application creation."""
    owned: set[str] = set()

    def _add(path: object) -> None:
        if not path:
            return
        try:
            owned.add(os.path.realpath(os.path.expanduser(str(path).strip())))
        except Exception:
            return

    _add(workdir)
    for item in tool_reinstall_roots(env):
        _add(item)
    _add(env.get(VAULT_ENV))
    for item in (env.get(EXTRA_WRITE_ROOTS_ENV) or "").split(os.pathsep):
        _add(item)
    for item in (env.get(SANDBOX_RW_ENV) or "").split(os.pathsep):
        _add(item)
    for item in extra_roots or ():
        _add(item)
    return owned


def _prepare_bind_root(root: str, owned: set[str]) -> str | None:
    """Resolves the absolute path of the root directory; if the app-owned root does not exist, it is created. If the result is `None`, skip it."""
    try:
        path = os.path.realpath(os.path.expanduser(str(root).strip()))
    except Exception:
        return None
    if not path or path == "/":
        return None
    if os.path.exists(path):
        return path
    if path in owned:
        try:
            os.makedirs(path, mode=0o700, exist_ok=True)
        except OSError:
            pass
        if os.path.exists(path):
            return path
    return None


def wrap_with_sandbox_argv(
    command: str,
    *,
    workdir: str,
    extra_roots: Iterable[str] | None = None,
    env: dict[str, str] | None = None,
    guest_mode: bool | None = None,
) -> list[str] | None:
    """Constructs `argv` for `bwrap` with read-only `/` and write access restricted to allowed directories.

    Returns `argv` or `None` if `bwrap` is not found. An absent `bind` target does not cancel the wrapper: the root is logged with telemetry `bind_target_missing`.

    `guest_mode=None` resolves from `IGOREK_ISOLATION_GUEST`. Inside a guest, the flag block becomes a hardened `--unshare-all`-class prefix, and the environment is rebuilt explicitly (`--clearenv` + `--setenv` allowlist); outside a guest, the `argv` remains byte-identical to its original state.
    """
    env = env if env is not None else os.environ
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return None
    real_workdir = os.path.realpath(os.path.expanduser(str(workdir))) if workdir else ""
    owned = _app_owned_write_roots(workdir, extra_roots=extra_roots, env=env)

    from .isolation.guest_bwrap import (
        guest_mode_requested,
        guest_sandbox_env_argv,
        hardened_argv_prefix,
    )

    if guest_mode is None:
        guest_mode = guest_mode_requested(env)

    if guest_mode:
        argv = hardened_argv_prefix(bwrap)
        argv += [
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
        ]
        argv += guest_sandbox_env_argv(env)
    else:
        argv = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
        ]

    bind_roots: list[str] = list(
        allowed_write_roots(workdir, extra_roots=extra_roots, env=env)
    )
    for item in (env.get(SANDBOX_RW_ENV) or "").split(os.pathsep):
        candidate = item.strip()
        if candidate:
            bind_roots.append(candidate)

    seen: set[str] = set()
    for root in bind_roots:
        prepared = _prepare_bind_root(root, owned)
        if prepared is None:
            _record_telemetry(
                "sandbox_error",
                command,
                ScopeViolation(WriteTarget(root, "bind_target_missing", "bwrap"), []),
                real_workdir,
                "sandbox",
            )
            continue
        if prepared in seen:
            continue
        seen.add(prepared)
        argv += ["--bind", prepared, prepared]

    from .host_services import protected_paths
    private_paths = protected_paths(env)
    import pwd
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    units = home / '.config/systemd/user'
    bus = Path('/run/user') / str(os.getuid())
    if units.is_dir():
        argv += ['--ro-bind', str(units), str(units)]
    if bus.is_dir():
        argv += ['--tmpfs', str(bus)]
    argv += ['--unsetenv', 'DBUS_SESSION_BUS_ADDRESS', '--unsetenv', 'DBUS_SYSTEM_BUS_ADDRESS']
    for private in private_paths:
        if private.is_dir():
            argv += ['--tmpfs', str(private)]
        elif private.is_file():
            argv += ['--ro-bind', str(private), str(private)]

    argv += ["--chdir", real_workdir, "--", "/bin/bash", "-c", command]
    return argv


def wrap_with_sandbox(
    command: str,
    *,
    workdir: str,
    extra_roots: Iterable[str] | None = None,
    filesystem_mode: str = "workspace",
    env: dict[str, str] | None = None,
) -> str | None:
    """A lightweight wrapper around the `argv`-version: returns a ready command string or `None`.

    The sandbox does not depend on `filesystem_mode` (argument retained for compatibility).
    `IGOREK_BASH_SANDBOX=off`/`observe` returns `None`—the caller decides whether to execute the command without isolation.
    """
    env = env if env is not None else os.environ
    if resolve_sandbox_mode(env) != "on":
        return None
    argv = wrap_with_sandbox_argv(
        command, workdir=workdir, extra_roots=extra_roots, env=env
    )
    if argv is None:
        return None
    return shlex.join(argv)
