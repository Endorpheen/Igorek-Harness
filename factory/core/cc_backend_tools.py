"""Tool definitions and execution for cc-backend.

Provides Anthropic Messages API tool schemas and local execution
for the basic tool set (Write, Read, Bash, Edit, Glob, Grep, SessionSearch,
web_search, web_extract).

Each tool registers itself at module level via ``registry.register()``.
Callers use ``get_tool_schemas()`` and ``execute_tool()`` — thin wrappers
around the central ``ToolRegistry``.
"""

from __future__ import annotations

import glob as _glob
import ipaddress
import io
import json
import time
import hashlib
import logging
import mimetypes
import os
import re
import shlex
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable
from .artifact_files import open_regular_file, atomic_write_file, read_regular_text, private_io_directory

from .running_commands import command_context, run_process
from urllib.parse import urlparse

import sys

logger = logging.getLogger(__name__)

# Load bundled defaults and the configured data directory, never a private checkout.
_SOURCE_DIR = Path(__file__).resolve().parents[1]
_VAULT_ENV_ENABLED = str(os.environ.get("FACTORY_VAULT_ENABLED") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_EARLY_ENV_PATHS = [_SOURCE_DIR / "config" / "gateway.default.env"]
if not _VAULT_ENV_ENABLED:
    from .runtime import get_env_files_dir

    _EARLY_ENV_PATHS.extend([
        _SOURCE_DIR / ".env.gateway",
        _SOURCE_DIR / ".env",
        get_env_files_dir() / ".env.gateway",
        get_env_files_dir() / ".env",
    ])
else:
    logger.debug("Vault enabled; skipping local plaintext environment files")
for _env_path in _EARLY_ENV_PATHS:
    if _env_path.exists():
        logger.debug("Loading environment file %s", _env_path)
        for _raw_line in _env_path.read_text(encoding="utf-8").splitlines():
            _ln = _raw_line.strip()
            if _ln and not _ln.startswith("#") and "=" in _ln:
                _key, _val = _ln.split("=", 1)
                if "MCP" in _key:
                    logger.debug("Loading environment setting %s", _key)
                os.environ.setdefault(_key.strip(), _val.strip().strip("'").strip('"'))

from .checkpoint_manager import CheckpointManager
from .session_search import session_search
from .tool_registry import registry, tool_error, tool_result

SYSTEM_BLOCKED_ROOTS = (
    "/etc",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/boot",
    "/root",
)

# Device paths that must never be read (infinite output or blocking input)
_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/full",
    "/dev/stdin",
    "/dev/tty",
    "/dev/console",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/fd/0",
    "/dev/fd/1",
    "/dev/fd/2",
})

# User-selected broad read roots; workspace mode remains restricted.
USER_READ_ROOTS = tuple(
    str(path)
    for path in (
        Path.home(),
        Path("/mnt"),
        Path("/media"),
        Path("/tmp"),
    )
)
IMAGE_MIME_PREFIX = "image/"
TAVILY_BASE_URL = "https://api.tavily.com"


def web_tools_available() -> bool:
    from .web_tools_runtime import active_web_tools
    provider = active_web_tools()
    if provider is not None:
        return provider.available()
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def _agent_browser_path() -> str:
    """Locate the ``agent-browser`` CLI (Hermes browser pattern)."""
    exe = shutil.which("agent-browser")
    if exe:
        return exe
    for cand in (
        Path.home() / ".cargo" / "bin" / "agent-browser",
        Path("/usr/local/bin/agent-browser"),
        Path("/usr/bin/agent-browser"),
    ):
        if cand.is_file():
            return str(cand)
    return ""


def browser_tools_available() -> bool:
    """True when the browser tool can run (agent-browser CLI present)."""
    return bool(_agent_browser_path())


def _looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    text_bytes = sum(
        1 for byte in sample if byte in {9, 10, 13} or 32 <= byte <= 126 or byte >= 128
    )
    return text_bytes / max(1, len(sample)) < 0.85


def _readable_text_guard(path: str, sample: bytes | None = None) -> str | None:
    mime_type, _ = mimetypes.guess_type(path)
    if str(mime_type or "").startswith(IMAGE_MIME_PREFIX):
        return (
            "Этот файл является изображением и не читается текстовым Read. "
            "Используй уже прикреплённый image-блок в текущем запросе, чтобы описать картинку."
        )
    if sample is None:
        try:
            with open_regular_file(path) as handle:
                sample = handle.read(4096)
        except OSError as exc:
            return f"Error reading file: {exc}"
    if _looks_binary(sample):
        return "Этот файл выглядит бинарным и не читается текстовым Read."
    return None


# ---------------------------------------------------------------------------
# Path / filesystem validation helpers
# ---------------------------------------------------------------------------


def _is_within_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _is_in_any_root(path: str, roots: tuple[str, ...]) -> bool:
    return any(_is_within_root(path, root) for root in roots)


def _resolve_mode(mode: str | None) -> str:
    normalized = str(mode or "workspace").strip().lower()
    if normalized not in {"workspace", "user-read", "full-dangerous"}:
        return "workspace"
    return normalized


def _validate_system_block(path: str) -> None:
    if _is_in_any_root(path, SYSTEM_BLOCKED_ROOTS):
        raise ValueError(f"Path {path} is inside a blocked system directory")


def _validate_write_path(file_path: str, workdir: str, filesystem_mode: str) -> str:
    """Validate write/edit operations based on filesystem mode."""
    resolved = os.path.realpath(os.path.join(workdir, file_path))
    from .host_services import deny_control_path
    deny_control_path(resolved)
    wd = os.path.realpath(workdir)
    mode = _resolve_mode(filesystem_mode)

    if mode == "full-dangerous":
        _validate_system_block(resolved)
        return resolved

    if not _is_within_root(resolved, wd):
        # Allow writes to Obsidian vault
        vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
        if vault and _is_within_root(resolved, os.path.realpath(vault)):
            return resolved
        raise ValueError(f"Path {file_path} is outside working directory {workdir}")
    return resolved


def _validate_read_path(file_path: str, workdir: str, filesystem_mode: str) -> str:
    """Validate read operations based on filesystem mode."""
    resolved = os.path.realpath(os.path.join(workdir, file_path))
    from .host_services import deny_control_path
    deny_control_path(resolved)
    wd = os.path.realpath(workdir)
    mode = _resolve_mode(filesystem_mode)

    if mode == "workspace":
        if not _is_within_root(resolved, wd):
            raise ValueError(f"Path {file_path} is outside working directory {workdir}")
        return resolved

    _validate_system_block(resolved)
    if mode == "full-dangerous":
        return resolved

    if _is_within_root(resolved, wd):
        return resolved
    if _is_in_any_root(resolved, USER_READ_ROOTS):
        return resolved
    raise ValueError(
        f"Path {file_path} is outside allowed user-read roots "
        f"({', '.join(USER_READ_ROOTS)})"
    )


# ---------------------------------------------------------------------------
# Tavily helpers
# ---------------------------------------------------------------------------


def _get_tavily_api_key() -> str:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise ValueError("TAVILY_API_KEY is not configured")
    return api_key


def _tavily_request(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .web_tools_runtime import active_web_tools
    provider = active_web_tools()
    if provider is not None:
        return provider.request(endpoint, payload)
    api_key = _get_tavily_api_key()
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{TAVILY_BASE_URL}/{endpoint.lstrip('/')}",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = (
            exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
        )
        raise RuntimeError(
            f"Tavily API error {exc.code}: {body or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Tavily connection error: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Tavily returned invalid JSON: {raw[:400]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Tavily returned non-object JSON")
    return parsed


def _normalize_tavily_search_results(response: dict[str, Any]) -> dict[str, Any]:
    web_results: list[dict[str, Any]] = []
    for index, item in enumerate(response.get("results", []), start=1):
        if not isinstance(item, dict):
            continue
        web_results.append(
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip(),
                "description": str(item.get("content", "")).strip(),
                "position": index,
            }
        )
    return {"success": True, "data": {"web": web_results}}


def _normalize_tavily_documents(response: dict[str, Any]) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    for item in response.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        raw_content = str(
            item.get("raw_content", "") or item.get("content", "")
        ).strip()
        documents.append(
            {
                "url": url,
                "title": str(item.get("title", "")).strip(),
                "content": raw_content,
                "raw_content": raw_content,
                "metadata": {
                    "sourceURL": url,
                    "title": str(item.get("title", "")).strip(),
                },
            }
        )
    for item in response.get("failed_results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        documents.append(
            {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": str(item.get("error", "extraction failed")).strip(),
                "metadata": {"sourceURL": url},
            }
        )
    for failed_url in response.get("failed_urls", []):
        url = str(failed_url).strip()
        if not url:
            continue
        documents.append(
            {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": "extraction failed",
                "metadata": {"sourceURL": url},
            }
        )
    return {"success": True, "data": {"documents": documents}}


def _validate_web_urls(urls: list[Any]) -> list[str]:
    normalized: list[str] = []
    for raw in urls:
        url = str(raw or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid URL: {url}")
        normalized.append(url)
    if not normalized:
        raise ValueError("urls must contain at least one valid http/https URL")
    return normalized[:10]


# ---------------------------------------------------------------------------
# Tool handler implementations
# ---------------------------------------------------------------------------


def _authorized_write_root(path: str, workdir: str) -> str:
    root = os.path.realpath(workdir)
    if _is_within_root(path, root):
        return root
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if vault and _is_within_root(path, os.path.realpath(vault)):
        return os.path.realpath(vault)
    return str(Path(path).parent)


def _tool_write(
    data: dict[str, Any],
    workdir: str,
    filesystem_mode: str,
    checkpoint_manager: CheckpointManager | None,
    change_tracker: Any = None,
) -> str:
    path = _validate_write_path(data["file_path"], workdir, filesystem_mode)
    content = data["content"]
    if checkpoint_manager is not None:
        checkpoint_manager.ensure_checkpoint_for_path(
            target_path=path,
            workdir=workdir,
            reason="before Write",
            authorized_root=_authorized_write_root(path, workdir),
        )

    # JSON validation for .json/.excalidraw files
    json_fixed = False
    json_suffixes = (".json", ".excalidraw")
    if isinstance(content, str) and any(path.endswith(s) for s in json_suffixes):
        try:
            json.loads(content)
        except json.JSONDecodeError:
            # Attempt to fix: remove JSONC comments and reformat
            try:
                from .output_contracts import _strip_jsonc_comments
                cleaned = _strip_jsonc_comments(content)
                parsed = json.loads(cleaned)
                content = json.dumps(parsed, ensure_ascii=False, indent=2)
                json_fixed = True
            except Exception:
                pass  # Record as-is

    atomic_write_file(path, content)

    if change_tracker is not None:
        try:
            change_tracker.record_write(path, content)
        except Exception:
            pass

    result = f"File written successfully: {path}"
    if json_fixed:
        result += " (JSON нормализован: убраны комментарии, добавлено форматирование)"

    # Hint: if .md was written to workdir but Obsidian vault exists, suggest re-writing
    vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
    if vault and path.endswith(".md") and path.startswith(os.path.realpath(workdir)):
        basename = os.path.basename(path)
        vault_target = os.path.join(vault, basename)
        result += (
            f"\nВНИМАНИЕ: файл записан в workdir, а не в Obsidian vault. "
            f"Если это заметка для Obsidian — перезапиши в {vault_target}"
        )

    return result


def _is_blocked_device(filepath: str) -> bool:
    """Return True if the path would hang the process."""
    normalized = os.path.expanduser(filepath)
    if normalized in _BLOCKED_DEVICE_PATHS:
        return True
    if normalized.startswith("/proc/") and normalized.endswith(
        ("/fd/0", "/fd/1", "/fd/2")
    ):
        return True
    return False


def _tool_read(data: dict[str, Any], workdir: str, filesystem_mode: str,
               *, tool_result_access=None, read_context_window_tokens=None, read_max_chars=None) -> str:
    from .host_services import deny_control_path
    from .read_paging import read_budget, read_text_page

    raw_path = data["file_path"]
    if _is_blocked_device(raw_path):
        return f"Cannot read '{raw_path}': this is a device file that would block or produce infinite output."
    handle = None
    try:
        path = _validate_read_path(raw_path, workdir, filesystem_mode)
    except ValueError:
        candidate = os.path.abspath(os.path.join(workdir, raw_path))
        deny_control_path(candidate)
        if tool_result_access is not None:
            handle = tool_result_access.open_issued(candidate)
        if handle is None:
            raise
        path = candidate
    else:
        if not os.path.exists(path):
            return f"File not found: {path}"
        handle = open_regular_file(path)
    with handle:
        guard = _readable_text_guard(path, handle.read(4096))
        if guard is not None:
            return guard
        handle.seek(0)
        offset = max(0, int(data.get("offset") or 0))
        limit = int(data["limit"]) if data.get("limit") is not None else None
        cursor = data.get("cursor", 0)
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
        return read_text_page(text, offset=offset, limit=limit, cursor=cursor,
                              max_chars=read_budget(read_context_window_tokens, read_max_chars))


def _tool_edit(
    data: dict[str, Any],
    workdir: str,
    filesystem_mode: str,
    checkpoint_manager: CheckpointManager | None,
    change_tracker: Any = None,
) -> str:
    path = _validate_write_path(data["file_path"], workdir, filesystem_mode)
    old_string = data.get("old_string")
    new_string = data.get("new_string")
    replace_all = data.get("replace_all", False)
    if not isinstance(old_string, str) or not old_string.strip():
        return (
            "old_string cannot be empty.\n"
            "Hint: Read the current file content first, then retry Edit with a "
            "concrete old_string copied from the file, or use Write with the "
            "full desired file content. Do not retry Edit with an empty old_string."
        )
    if not isinstance(new_string, str):
        return "new_string must be a string"
    if old_string == new_string:
        return "new_string must differ from old_string"
    if not os.path.exists(path):
        return f"File not found: {path}"
    if checkpoint_manager is not None:
        checkpoint_manager.ensure_checkpoint_for_path(
            target_path=path,
            workdir=workdir,
            reason="before Edit",
            authorized_root=_authorized_write_root(path, workdir),
        )
    content = read_regular_text(path)
    if old_string not in content:
        return f"old_string not found in {path}"
    if replace_all:
        content = content.replace(old_string, new_string)
    else:
        content = content.replace(old_string, new_string, 1)
    atomic_write_file(path, content)
    if change_tracker is not None:
        try:
            change_tracker.record_edit(path, old_string, new_string)
        except Exception:
            pass
    return f"File edited successfully: {path}"


def _bash_scope_denial(
    command: str,
    *,
    workdir: str,
    filesystem_mode: str,
    guard_mode: str | None = None,
) -> str | None:
    """Restriction on writing outside the working directory (factory/core/bash_scope_guard.py)."""

    def _log_fail_open(stage: str, exc: BaseException) -> None:
        """Guard disabled (fail-open): loud output to `stderr` + best-effort line in JSONL format."""
        try:
            print(
                f"[bash_scope_guard] ОТКЛЮЧЁН ({stage}): {exc!r} — команда "
                "выполняется БЕЗ проверки области записи (fail-open)",
                file=sys.stderr,
            )
        except Exception:
            pass
        try:
            import json as _json
            import os as _os
            from datetime import datetime as _dt, timezone as _tz

            path = _os.path.join(
                _os.path.expanduser("~"), ".cache", "igorek-bash-scope", "denials.jsonl"
            )
            _os.makedirs(_os.path.dirname(path), mode=0o700, exist_ok=True)
            line = _json.dumps(
                {
                    "ts": _dt.now(_tz.utc).isoformat(),
                    "verdict": "fail_open",
                    "mode": "fail_open",
                    "stage": stage,
                    "error": repr(exc)[:200],
                    "workdir": workdir,
                },
                ensure_ascii=False,
            ) + chr(10)
            fd = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_APPEND, 0o600)
            try:
                _os.write(fd, line.encode("utf-8"))
            finally:
                _os.close(fd)
        except Exception:
            pass

    try:
        from .bash_scope_guard import enforce_bash_scope
    except Exception as exc:
        _log_fail_open("import", exc)
        return None
    try:
        verdict, text = enforce_bash_scope(
            command,
            workdir=workdir,
            filesystem_mode=filesystem_mode,
            guard_mode=guard_mode,
        )
    except Exception as exc:
        _log_fail_open("enforce", exc)
        return None
    if verdict == "deny":
        return text
    if verdict == "warn":
        print(f"[bash_scope_guard] {text}", file=sys.stderr)
    return None


def _bash_sandboxed_argv(
    command: str,
    *,
    workdir: str,
    extra_roots: Iterable[str] | None = None,
) -> list[str] | None:
    """Compile `bwrap` command arguments (None — bwrap unavailable/build error)."""
    try:
        from .bash_scope_guard import wrap_with_sandbox_argv
    except Exception:
        return None
    try:
        return wrap_with_sandbox_argv(
            command,
            workdir=workdir,
            extra_roots=extra_roots,
        )
    except Exception:
        return None


def _bash_sandboxed_command(
    command: str,
    *,
    workdir: str,
    filesystem_mode: str,
    guard_mode: str | None = None,
) -> str:
    """String wrapper around the argv version (backward compatibility).

    `filesystem_mode`/`guard_mode` are used for compatibility with the calling code;
    sandbox is independent of security levels. If bwrap is unavailable, the command
    returns unchanged.
    """
    argv = _bash_sandboxed_argv(command, workdir=workdir)
    if argv is None:
        return command
    return shlex.join(argv)


def _sandbox_status_safe() -> dict[str, object] | None:
    """Sandbox status (part 1); None if the module/database is unavailable."""
    try:
        from .bash_sandbox_settings import sandbox_status
    except Exception:
        return None
    try:
        return sandbox_status()
    except Exception:
        return None


def _sandbox_denied_text(reason: str) -> str:
    """Fail-closed text (part 1) with a local fallback option."""
    try:
        from .bash_scope_guard import sandbox_denied_message
    except Exception:
        return f"SANDBOX_UNAVAILABLE: {reason}."
    try:
        return sandbox_denied_message(reason)
    except Exception:
        return f"SANDBOX_UNAVAILABLE: {reason}."


def _record_sandbox_telemetry(verdict: str, command: str, workdir: str) -> None:
    """Best-effort telemetry for sandbox via a shared JSONL guard."""
    try:
        from .bash_scope_guard import _record_telemetry
    except Exception:
        return
    try:
        _record_telemetry(verdict, command, None, workdir, "sandbox")
    except Exception:
        return


def _sandbox_spawn_error(result: subprocess.CompletedProcess[str]) -> bool:
    """bwrap failed to create a namespace (not just the command returning a non-zero exit code).

    The marker is counted only for lines marked by bwrap with its own prefix (`bwrap:` / basename of the binary): `Operation not permitted` from the command itself is its actual failure, not sandbox unavailability.
    """
    if result.returncode == 0:
        return False
    try:
        from .bash_scope_guard import _NAMESPACE_BLOCKED_MARKERS
    except Exception:
        return False
    bwrap_prefix = os.path.basename(shutil.which("bwrap") or "bwrap") + ":"
    bwrap_lines: list[str] = []
    for line in (result.stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(bwrap_prefix):
            bwrap_lines.append(stripped)
    return any(
        marker in line for line in bwrap_lines for marker in _NAMESPACE_BLOCKED_MARKERS
    )


def _format_bash_result(result: subprocess.CompletedProcess[str]) -> str:
    """Output model format — byte-by-byte, as in legacy paths."""
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += ("\n--- stderr ---\n" + result.stderr) if output else result.stderr
    if result.returncode != 0:
        output += f"\n(exit code: {result.returncode})"
    return output or "(Bash completed with no output)"


def _run_bash_legacy(command: str, *, workdir: str, timeout_s: float) -> str:
    """Explicitly disabled sandbox: retains default behavior with `shell=True`."""
    try:
        result = run_process(
            command,
            label=command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_s}s"
    except Exception as exc:
        return f"Error executing command: {exc}"
    return _format_bash_result(result)


def _run_bash_sandboxed(
    argv: list[str], *, command: str, workdir: str, timeout_s: float
) -> str:
    """Isolated execution: `argv` with `shell=False`, no environment variables, `cwd=workdir`."""
    try:
        result = run_process(
            argv,
            label=command,
            shell=False,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_s}s"
    except Exception:
        _record_sandbox_telemetry("sandbox_error", command, workdir)
        return _sandbox_denied_text("bwrap_error")
    if _sandbox_spawn_error(result):
        _record_sandbox_telemetry("sandbox_error", command, workdir)
        return _sandbox_denied_text("bwrap_error")
    return _format_bash_result(result)


def _sudo_broker_command(command: str) -> list[str] | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) < 3 or argv[:2] != ["igorek-sudo", "--"]:
        return None
    return argv[2:]


def _tool_bash(
    data: dict[str, Any],
    workdir: str,
    filesystem_mode: str = "workspace",
    guard_mode: str | None = None,
) -> str:
    command = data["command"]
    if sys.platform == 'win32':
        return 'ERROR: Windows command sandbox is not available in this preview; no command was executed.'
    timeout_ms = data.get("timeout", 120000)
    timeout_s = min(timeout_ms / 1000, 600)
    sudo_argv = _sudo_broker_command(command)
    if sudo_argv is not None:
        from .isolation.guest_bwrap import guest_mode_requested
        privileged_guest = guest_mode_requested()
        if privileged_guest:
            # The normal Bash approval gate pauses the guest task before this
            # function runs. Once resumed, elevate only inside this same VM.
            argv = ["/usr/bin/sudo", "-n", "--", *sudo_argv]
            timeout = timeout_s
        else:
            from .sudo_broker import REQUEST_TTL_S

            argv = [
                sys.executable,
                "-m",
                "factory.core.sudo_broker",
                "--timeout",
                str(int(timeout_s)),
                "--",
                *sudo_argv,
            ]
            timeout = REQUEST_TTL_S + timeout_s + 15
        try:
            result = run_process(
                argv,
                label=command,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=timeout,
                check=False,
                **({'guest_privileged': True} if privileged_guest else {}),
            )
        except subprocess.TimeoutExpired:
            return "Sudo approval or command timed out"
        return _format_bash_result(result)
    denial = _bash_scope_denial(
        command,
        workdir=workdir,
        filesystem_mode=filesystem_mode,
        guard_mode=guard_mode,
    )
    if denial is not None:
        return denial

    # A per-session KVM guest is already the isolation boundary. Do not add a
    # second bubblewrap namespace around commands executed inside that guest.
    try:
        from .isolation.guest_bwrap import guest_mode_requested

        if guest_mode_requested():
            return _run_bash_legacy(command, workdir=workdir, timeout_s=timeout_s)
    except Exception:
        pass

    status = _sandbox_status_safe()
    if status is None:
        _record_sandbox_telemetry("sandbox_denied", command, workdir)
        return _sandbox_denied_text("sandbox_status_unavailable")

    reason = str(status.get("reason") or "")
    enabled = bool(status.get("enabled"))
    available = bool(status.get("available"))

    if reason in {"user_disabled", "env_disabled"}:
        return _run_bash_legacy(command, workdir=workdir, timeout_s=timeout_s)

    if reason == "observe":
        _bash_sandboxed_argv(command, workdir=workdir)
        _record_sandbox_telemetry("sandbox_observe", command, workdir)
        return _run_bash_legacy(command, workdir=workdir, timeout_s=timeout_s)

    if enabled and available:
        argv = _bash_sandboxed_argv(command, workdir=workdir)
        if argv is None:
            _record_sandbox_telemetry("sandbox_denied", command, workdir)
            return _sandbox_denied_text("bwrap_missing")
        return _run_bash_sandboxed(
            argv, command=command, workdir=workdir, timeout_s=timeout_s
        )

    _record_sandbox_telemetry("sandbox_denied", command, workdir)
    return _sandbox_denied_text(reason or "bwrap_missing")


def _allowed_glob_matches(pattern: str, workdir: str, filesystem_mode: str):
    absolute = os.path.join(os.path.realpath(workdir), pattern)
    parts = Path(absolute).parts
    first_magic = next((i for i, part in enumerate(parts) if _glob.has_magic(part)), len(parts))
    base = os.path.join(*parts[:first_magic])
    _validate_read_path(base, workdir, filesystem_mode)
    if first_magic == len(parts):
        yield from _glob.iglob(base)
        return

    def expand(directory, index, ancestors):
        if index == len(parts):
            yield directory
            return
        part = parts[index]
        recursive = part == "**"
        if recursive:
            yield from expand(directory, index + 1, ancestors)
        # Expand one component at a time so an external link is never traversed.
        for candidate in _glob.iglob(os.path.join(directory, "*" if recursive else part)):
            try:
                resolved = _validate_read_path(candidate, workdir, filesystem_mode)
            except ValueError:
                continue
            if recursive:
                if os.path.isdir(resolved) and resolved not in ancestors:
                    yield from expand(candidate, index, ancestors | {resolved})
                elif index == len(parts) - 1 and not os.path.isdir(resolved):
                    yield candidate
            else:
                yield from expand(candidate, index + 1, ancestors)

    yield from expand(base, first_magic, {os.path.realpath(base)})


def _tool_glob(data: dict[str, Any], workdir: str, filesystem_mode: str) -> str:
    pattern = data["pattern"]
    directories_only = pattern.endswith(os.sep)
    matches = [
        path + os.sep if directories_only and not path.endswith(os.sep) else path
        for path in _allowed_glob_matches(pattern, workdir, filesystem_mode)
        if not directories_only or os.path.isdir(path)
    ]
    matches.sort()
    return "\n".join(matches) if matches else "No files matched."


def _tool_grep(data: dict[str, Any], workdir: str, filesystem_mode: str) -> str:
    pattern = data["pattern"]
    raw_path = data.get("path") or "."
    search_path = _validate_read_path(os.path.join(workdir, raw_path), workdir, filesystem_mode)
    try:
        from .host_services import protected_paths
        exclusions = []
        for private in protected_paths():
            if private.resolve().is_relative_to(Path(search_path)):
                relative = '**/' + private.resolve().as_posix().lstrip('/')
                exclusions += ['--glob', '!' + relative + ('/**' if private.is_dir() else '')]
        result = subprocess.run(
            ["rg", "--no-heading", "-n", *exclusions, "-e", pattern, "--", search_path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        if result.returncode == 2:
            return f"Error: {result.stderr}"
        return result.stdout or "No matches found."
    except FileNotFoundError:
        return "ripgrep (rg) not found"
    except Exception as exc:
        return f"Error: {exc}"


def _tool_session_search(data: dict[str, Any], *, current_session: str | None) -> str:
    return session_search(
        query=str(data.get("query") or ""),
        current_session=current_session,
        limit=int(data.get("limit") or 3),
        role_filter=str(data.get("role_filter") or "user,assistant"),
        mode=str(data.get("mode") or "hybrid"),
    )


def _tool_web_search(data: dict[str, Any]) -> str:
    query = str(data.get("query", "")).strip()
    if not query:
        return json.dumps(
            {"success": False, "error": "query is required"},
            ensure_ascii=False,
            indent=2,
        )
    limit = data.get("limit", 5)
    try:
        limit_int = max(1, min(int(limit), 10))
    except Exception:
        limit_int = 5
    try:
        response = _tavily_request(
            "search",
            {
                "query": query,
                "max_results": limit_int,
                "include_raw_content": False,
                "include_images": False,
            },
        )
        return json.dumps(
            _normalize_tavily_search_results(response), ensure_ascii=False, indent=2
        )
    except Exception as exc:
        return json.dumps(
            {"success": False, "error": str(exc)}, ensure_ascii=False, indent=2
        )


def _tool_web_extract(data: dict[str, Any]) -> str:
    urls = data.get("urls")
    if not isinstance(urls, list):
        return json.dumps(
            {"success": False, "error": "urls must be a list of URLs"},
            ensure_ascii=False,
            indent=2,
        )
    try:
        valid_urls = _validate_web_urls(urls)
        response = _tavily_request(
            "extract",
            {
                "urls": valid_urls,
                "include_images": False,
            },
        )
        return json.dumps(
            _normalize_tavily_documents(response), ensure_ascii=False, indent=2
        )
    except Exception as exc:
        return json.dumps(
            {"success": False, "error": str(exc)}, ensure_ascii=False, indent=2
        )


# ---------------------------------------------------------------------------
# Registry adapters — (args, **kwargs) → handler
# ---------------------------------------------------------------------------


def _check_file_tools() -> bool:
    return True


def _handle_write(args: dict[str, Any], **kwargs: Any) -> str:
    return _tool_write(
        args,
        workdir=kwargs["workdir"],
        filesystem_mode=kwargs.get("filesystem_mode", "workspace"),
        checkpoint_manager=kwargs.get("checkpoint_manager"),
        change_tracker=kwargs.get("change_tracker"),
    )


def _handle_read(args: dict[str, Any], **kwargs: Any) -> str:
    return _tool_read(
        args, workdir=kwargs["workdir"], filesystem_mode=kwargs.get("filesystem_mode", "workspace"),
        tool_result_access=kwargs.get("tool_result_access"),
        read_context_window_tokens=kwargs.get("read_context_window_tokens"),
        read_max_chars=kwargs.get("read_max_chars"),
    )


def _handle_edit(args: dict[str, Any], **kwargs: Any) -> str:
    return _tool_edit(
        args,
        workdir=kwargs["workdir"],
        filesystem_mode=kwargs.get("filesystem_mode", "workspace"),
        checkpoint_manager=kwargs.get("checkpoint_manager"),
        change_tracker=kwargs.get("change_tracker"),
    )


def _handle_bash(args: dict[str, Any], **kwargs: Any) -> str:
    tracker = kwargs.get("change_tracker")
    try:
        before = tracker.workspace_snapshot() if tracker is not None else None
    except OSError:
        before = None
    with command_context(kwargs.get("current_session"), kwargs.get("task_id"), kwargs.get("tool_use_id")):
        try:
            return _tool_bash(
                args,
                workdir=kwargs["workdir"],
                filesystem_mode=str(kwargs.get("filesystem_mode") or "workspace"),
                guard_mode=kwargs.get("guard_mode"),
            )
        finally:
            if tracker is not None:
                try:
                    tracker.record_shell_changes(before)
                except OSError:
                    logger.warning("Shell change tracking could not be persisted")


def _handle_glob(args: dict[str, Any], **kwargs: Any) -> str:
    return _tool_glob(
        args,
        workdir=kwargs["workdir"],
        filesystem_mode=kwargs.get("filesystem_mode", "workspace"),
    )


def _handle_grep(args: dict[str, Any], **kwargs: Any) -> str:
    return _tool_grep(
        args,
        workdir=kwargs["workdir"],
        filesystem_mode=kwargs.get("filesystem_mode", "workspace"),
    )


def _handle_session_search(args: dict[str, Any], **kwargs: Any) -> str:
    provider = kwargs.get("session_search_provider")
    if callable(provider):
        return str(provider(args))
    return _tool_session_search(args, current_session=kwargs.get("current_session"))


def _handle_memory_forget(args: dict[str, Any], **kwargs: Any) -> str:
    provider = kwargs.get("memory_forget_provider")
    if callable(provider):
        return str(provider(args))
    return execute_isolated_memory_forget(
        args, user_message=str(kwargs.get("user_message") or ""),
        confirm=lambda target: _confirm_memory_forget(target, **kwargs),
    )


def _confirm_memory_forget(target: str, **kwargs) -> bool:
    payload = {'title': 'Удаление сохранённого факта', 'questions': [{
        'id': 'memory_forget_decision', 'header': 'Память',
        'question': 'Удалить из сохранённых фактов ' + json.dumps(target, ensure_ascii=False) + '?',
        'options': [{'label': 'Сохранить факт', 'description': 'Оставить память без изменений.'},
                    {'label': 'Удалить факт', 'description': 'Удалить только этот точный факт.'}],
    }]}
    result = json.loads(_handle_request_user_input(payload, **kwargs))
    if not isinstance(result, dict) or result.get('cancelled'):
        return False
    answers = result.get('answers')
    answer = answers.get('memory_forget_decision') if isinstance(answers, dict) else None
    return isinstance(answer, dict) and answer.get('selected_options') == ['Удалить факт']


def execute_isolated_memory_forget(
    args: dict[str, Any], *, user_message: str, confirm=None, task_id: str = '', timeout_s: float = 1200,
) -> str:
    """Forget one exact Saved facts bullet after owner-controlled confirmation."""
    import json

    target = args.get("target")
    if not isinstance(target, str) or not target.strip() or len(target) > 500:
        return json.dumps({"changed": False, "rejected": True, "reason": "invalid_target"})
    if confirm is None and task_id:
        confirm = lambda value: _confirm_memory_forget(value, task_id=task_id, timeout_s=timeout_s)
    if confirm is None:
        return json.dumps({'changed': False, 'rejected': True, 'reason': 'owner_confirmation_required'})
    try:
        approved = confirm(target) is True
    except Exception:
        return json.dumps({'changed': False, 'rejected': True, 'reason': 'owner_confirmation_unavailable'})
    if not approved:
        return json.dumps({'changed': False, 'rejected': True, 'reason': 'owner_declined'})
    from .memory_policy import edit_saved_memory_fact

    return json.dumps(edit_saved_memory_fact(target), ensure_ascii=False)


def _handle_web_search(args: dict[str, Any], **kwargs: Any) -> str:
    provider = kwargs.get("web_tool_provider")
    if callable(provider):
        return str(provider("search", args))
    return _tool_web_search(args)


def _handle_web_extract(args: dict[str, Any], **kwargs: Any) -> str:
    provider = kwargs.get("web_tool_provider")
    if callable(provider):
        return str(provider("extract", args))
    return _tool_web_extract(args)


def execute_isolated_session_search(
    args: dict[str, Any], *, current_session: str | None = None
) -> str:
    """Run the fixed SessionSearch tool against host-owned session stores."""
    query = args.get("query", "")
    limit = args.get("limit", 3)
    role_filter = args.get("role_filter", "user,assistant")
    mode = args.get("mode", "hybrid")
    if not isinstance(query, str) or len(query) > 4096:
        raise ValueError("invalid session search query")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("invalid session search limit")
    if not isinstance(role_filter, str) or len(role_filter) > 64:
        raise ValueError("invalid session search role filter")
    if mode not in {"lexical", "semantic", "hybrid"}:
        raise ValueError("invalid session search mode")
    return _tool_session_search(
        {"query": query, "limit": limit, "role_filter": role_filter, "mode": mode},
        current_session=current_session,
    )


def execute_isolated_web_tool(operation: str, args: dict[str, Any]) -> str:
    """Host-side entry point for the KVM RPC; no guest URL or command is executed."""
    if operation == "search":
        query = args.get("query")
        limit = args.get("limit", 5)
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise ValueError("invalid web search query")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("invalid web search limit")
        result = _tool_web_search({"query": query, "limit": limit})
        return _redact_isolated_web_secret(result)
    if operation == "extract":
        urls = args.get("urls")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10:
            raise ValueError("invalid web extract URLs")
        safe_urls = _validate_isolated_extract_urls(urls)
        result = _tool_web_extract({"urls": safe_urls})
        return _redact_isolated_web_secret(result)
    raise ValueError("unsupported isolated web operation")


def _validate_isolated_extract_urls(urls: list[Any]) -> list[str]:
    """Keep remote extraction from being used as an internal-address fetcher."""
    normalized = _validate_web_urls(urls)
    for url in normalized:
        parsed = urlparse(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("web_extract accepts public internet URLs only") from exc
        if parsed.username or parsed.password or port not in (None, 80, 443):
            raise ValueError("web_extract accepts public internet URLs only")
        if not host or host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal", ".lan")):
            raise ValueError("web_extract accepts public internet URLs only")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if not address.is_global:
            raise ValueError("web_extract accepts public internet URLs only")
    return normalized


def _redact_isolated_web_secret(result: str) -> str:
    """Do not reflect the host API credential to the guest, even from upstream output."""
    secret = os.environ.get("TAVILY_API_KEY", "").strip()
    return result.replace(secret, "[redacted]") if secret else result


def _handle_update_plan(args: dict[str, Any], **kwargs: Any) -> str:
    plan = args.get("plan")
    if not isinstance(plan, list):
        raise ValueError("plan must be an array")
    explanation = args.get("explanation")
    if explanation is not None and not isinstance(explanation, str):
        raise ValueError("explanation must be a string")
    in_progress_count = 0
    for index, item in enumerate(plan):
        if not isinstance(item, dict):
            raise ValueError(f"plan[{index}] must be an object")
        unexpected = sorted(set(item) - {"step", "status"})
        if unexpected:
            raise ValueError(f"plan[{index}] has unknown fields: {unexpected}")
        if not isinstance(item.get("step"), str):
            raise ValueError(f"plan[{index}].step must be a string")
        status = item.get("status")
        if status not in {"pending", "in_progress", "completed"}:
            raise ValueError(f"plan[{index}].status is invalid: {status!r}")
        if status == "in_progress":
            in_progress_count += 1
    if in_progress_count > 1:
        raise ValueError("At most one step can be in_progress at a time")
    return "Plan updated"


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic Messages API format) — module-level constants
# ---------------------------------------------------------------------------

WRITE_SCHEMA: dict[str, Any] = {
    "name": "Write",
    "description": "Writes a file to the local filesystem.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be in the working directory).",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file.",
            },
            "reason": {
                "type": "string",
                "description": "Optional short visible reason shown in the approval UI.",
            },
            "justification": {
                "type": "string",
                "description": "Optional short visible justification shown in the approval UI.",
            },
        },
        "required": ["file_path", "content"],
    },
}

READ_SCHEMA: dict[str, Any] = {
    "name": "Read",
    "description": "Reads a bounded page of a file. Use offset/limit for lines (0-based), or cursor with offset to continue inside a long line. If a page is truncated, continue on the same file using the returned parameters; do not re-run the action that produced a saved result.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (0-indexed).",
            },
            "cursor": {"type": "integer", "minimum": 0, "description": "Character position within the starting line; use the value returned by a truncated Read page."},
            "limit": {
                "type": "integer",
                "description": "Number of lines to read.",
            },
        },
        "required": ["file_path"],
    },
}

EDIT_SCHEMA: dict[str, Any] = {
    "name": "Edit",
    "description": "Performs exact string replacements in files.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify.",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace.",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences.",
            },
            "reason": {
                "type": "string",
                "description": "Optional short visible reason shown in the approval UI.",
            },
            "justification": {
                "type": "string",
                "description": "Optional short visible justification shown in the approval UI.",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}

BASH_SCHEMA: dict[str, Any] = {
    "name": "Bash",
    "description": (
        "Executes a bash command in a shell."
        " Do not use Bash for web access, web search, fetching URLs, or curl-based API "
        "calls when dedicated web/browser tools are available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds (max 600000).",
            },
            "reason": {
                "type": "string",
                "description": "Optional short visible reason shown in the approval UI.",
            },
            "justification": {
                "type": "string",
                "description": "Optional short visible justification shown in the approval UI.",
            },
        },
        "required": ["command"],
    },
}

GLOB_SCHEMA: dict[str, Any] = {
    "name": "Glob",
    "description": "Fast file pattern matching tool.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against.",
            },
        },
        "required": ["pattern"],
    },
}

GREP_SCHEMA: dict[str, Any] = {
    "name": "Grep",
    "description": "Search file contents with regex patterns.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in.",
            },
        },
        "required": ["pattern"],
    },
}

SESSION_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "SessionSearch",
    "description": (
        "Search past Telegram/TUI sessions or list recent sessions. "
        "Use this to recall what was discussed or done in earlier conversations. "
        "Call without query to browse recent sessions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional query for past sessions. Omit or pass empty string to list recent sessions.",
            },
            "role_filter": {
                "type": "string",
                "description": "Optional comma-separated roles to search in: user,assistant.",
            },
            "mode": {
                "type": "string",
                "description": "Search mode: lexical, semantic, or hybrid. Default hybrid. Uses semantic embeddings when available, falls back to lexical if not.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of sessions to return (default 3, max 5).",
            },
        },
        "required": [],
    },
}

MEMORY_FORGET_SCHEMA: dict[str, Any] = {
    "name": "memory_forget",
    "description": (
        "Удаляет один точный факт из долговременной памяти после отдельного подтверждения владельца. "
        "Работает только с одним пунктом раздела Saved facts в MEMORY.md."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Точный текст пункта Saved facts, который пользователь просит удалить."}
        },
        "required": ["target"],
    },
}


UPDATE_PLAN_SCHEMA: dict[str, Any] = {
    "name": "update_plan",
    "description": (
        "Updates the task plan. "
        "Provide an optional explanation and a list of plan items, each with a step and status. "
        "At most one step can be in_progress at a time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "explanation": {
                "type": "string",
                "description": "Optional explanation of the plan update.",
            },
            "plan": {
                "type": "array",
                "description": "The list of steps.",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string", "description": "The plan step."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "The status of the step.",
                        },
                    },
                    "required": ["step", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["plan"],
        "additionalProperties": False,
    },
}

WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the web for current information. "
        "Use this for fresh facts, recent events, identifying movies/pages, or when the user asks about something online. "
        "For general internet search this is the primary tool; do not replace it with Bash/curl unless the user explicitly asks for shell access or the dedicated web tools have already failed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for the web.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5, max 10).",
            },
        },
        "required": ["query"],
    },
}

WEB_EXTRACT_SCHEMA: dict[str, Any] = {
    "name": "web_extract",
    "description": (
        "Fetch and extract the main content from one or more URLs. "
        "Prefer this when the user provides a specific link and asks what it is or what is on the page. "
        "If extraction fails, the next web step should normally be web_search, not Bash/curl."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of URLs to extract.",
            },
        },
        "required": ["urls"],
    },
}


# ---------------------------------------------------------------------------
# suggest_enable_mcp — structured capability suggestion
# ---------------------------------------------------------------------------

SUGGEST_ENABLE_MCP_SCHEMA: dict[str, Any] = {
    "name": "suggest_enable_mcp",
    "description": (
        "Ask the user to approve enabling a registered but currently disabled MCP server/capability. "
        "Use only for server_name values explicitly listed in Disabled MCP Capabilities. "
        "If the user approves, this tool enables the server for the current profile and attempts MCP hot reload."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "server_name": {
                "type": "string",
                "description": "Exact MCP server name from Disabled MCP Capabilities.",
            },
            "reason": {
                "type": "string",
                "description": "Short reason why this MCP capability is useful for the user request.",
            },
        },
        "required": ["server_name", "reason"],
    },
}


def _handle_suggest_enable_mcp(args: dict[str, Any], **kwargs: Any) -> str:
    server_name = str(args.get("server_name") or "").strip()
    reason = str(args.get("reason") or "").strip()
    profile = str(kwargs.get("profile") or "").strip()
    if not server_name:
        return tool_error("server_name is required")
    if not reason:
        return tool_error("reason is required")
    if not profile:
        return tool_error("profile is required to enable MCP server", server_name=server_name)
    try:
        from .mcp_config import (
            accept_mcp_enable_suggestion,
            get_profile_mcp_servers,
            list_mcp_servers,
        )
    except ImportError:
        from mcp_config import (
            accept_mcp_enable_suggestion,
            get_profile_mcp_servers,
            list_mcp_servers,
        )

    registry_payload = list_mcp_servers()
    entry = registry_payload.get(server_name)
    if not isinstance(entry, dict):
        return tool_error(
            "MCP server is not registered",
            server_name=server_name,
        )
    enabled = set(get_profile_mcp_servers(profile)) if profile else set()
    if server_name in enabled and entry.get("disabled") is not True:
        return tool_result(
            status="already_enabled",
            server_name=server_name,
            profile=profile or None,
            reason=reason,
        )
    # Telegram approval should not block the worker and lock the chat during waiting.
    approval = _ask_enable_mcp_approval(
        server_name=server_name,
        profile=profile,
        reason=reason,
        description=str(entry.get("description") or "").strip(),
        capabilities=[
            str(item).strip()
            for item in entry.get("capabilities") or []
            if str(item).strip()
        ],
        task_id=str(kwargs.get("task_id") or "").strip(),
        timeout_s=float(kwargs.get("timeout_s") or 1200),
        asynchronous=str(kwargs.get("source_platform") or "").strip().lower() == "telegram",
    )
    if not approval.get("approved"):
        return tool_result(
            status="suggestion_recorded",
            type="mcp_enable_suggestion",
            decision="rejected" if approval.get("decision") == "reject" else "pending",
            server_name=server_name,
            profile=profile or None,
            reason=reason,
            description=str(entry.get("description") or "").strip() or None,
            capabilities=[
                str(item).strip()
                for item in entry.get("capabilities") or []
                if str(item).strip()
            ],
            request_id=approval.get("request_id"),
            next_step="MCP enable suggestion was not accepted.",
        )
    enable_result = accept_mcp_enable_suggestion(profile, server_name)
    reloaded_entry = enable_result.get("server") if isinstance(enable_result, dict) else entry
    hot_reload = _hot_reload_mcp_server(server_name, reloaded_entry)
    return tool_result(
        status="enabled",
        type="mcp_enable_suggestion",
        server_name=server_name,
        profile=profile or None,
        reason=reason,
        description=str(entry.get("description") or "").strip() or None,
        capabilities=[
            str(item).strip()
            for item in entry.get("capabilities") or []
            if str(item).strip()
        ],
        enabled_for_profile=enable_result.get("enabled_for_profile", []),
        hot_reload=hot_reload,
        next_step=(
            "If hot_reload.status is ok and tools are listed, the MCP tools are available in this process. "
            "If hot_reload.status is error or unsupported, restart the chat after fixing the MCP server command."
        ),
    )


def _ask_enable_mcp_approval(
    *,
    server_name: str,
    profile: str,
    reason: str,
    description: str,
    capabilities: list[str],
    task_id: str,
    timeout_s: float,
    asynchronous: bool = False,
) -> dict[str, Any]:
    questions_raw = [
        {
            "id": "mcp_enable_decision",
            "header": "MCP",
            "question": (
                f"Включить MCP server {server_name!r} для профиля "
                f"{profile or '<unknown>'}?"
            ),
            "options": [
                {
                    "label": "Включить",
                    "description": "Снять disabled, добавить сервер в профиль и попробовать hot reload.",
                },
                {
                    "label": "Не включать",
                    "description": "Оставить сервер отключённым.",
                },
            ],
        }
    ]
    payload = {
        "title": "MCP enable suggestion",
        "questions": questions_raw,
        "context": {
            "server_name": server_name,
            "profile": profile,
            "reason": reason,
            "description": description,
            "capabilities": capabilities,
        },
    }
    if asynchronous and task_id:
        from .user_input_store import create_user_input_request

        request = create_user_input_request(task_id=task_id, payload=payload)
        return {
            "approved": False,
            "decision": "pending",
            "request_id": str(request.get("request_id") or ""),
        }

    raw = _handle_request_user_input(payload, task_id=task_id, timeout_s=timeout_s)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"approved": False, "decision": "invalid_response", "raw": raw}
    if result.get("cancelled"):
        return {"approved": False, "decision": "cancelled", "response": result}
    from .mcp_config import mcp_enable_answer_approved
    approved = mcp_enable_answer_approved(result)
    return {
        "approved": approved,
        "decision": "accept" if approved else "reject",
        "response": result,
    }


def _hot_reload_mcp_server(server_name: str, entry: Any) -> dict[str, Any]:
    try:
        from .mcp_bridge import reload_mcp_server_from_registry
    except ImportError:
        from mcp_bridge import reload_mcp_server_from_registry
    try:
        return reload_mcp_server_from_registry(
            server_name,
            entry if isinstance(entry, dict) else {},
        )
    except Exception as exc:
        return {
            "status": "error",
            "server_name": server_name,
            "error": f"{type(exc).__name__}: {exc}",
            "tools": [],
        }


# ---------------------------------------------------------------------------
# request_user_input — structured input form
# ---------------------------------------------------------------------------

REQUEST_USER_INPUT_SCHEMA: dict[str, Any] = {
    "name": "request_user_input",
    "description": (
        "Ask the user for structured input via an interactive form in the terminal. "
        "Use this when you need the user to provide information, make choices, or fill in multiple fields. "
        "Supports text input, single-select, multiselect, and secret (masked) fields."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Optional title for the input form.",
            },
            "questions": {
                "type": "array",
                "description": "List of questions to ask the user.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique identifier for this question.",
                        },
                        "header": {
                            "type": "string",
                            "description": "Section header or group label.",
                        },
                        "question": {
                            "type": "string",
                            "description": "The actual question text.",
                        },
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                            },
                            "description": "Available options for select/multiselect. Omit for text/secret fields.",
                        },
                        "is_secret": {
                            "type": "boolean",
                            "description": "If true, input is masked (passwords, API keys). Default: false.",
                        },
                        "is_multiselect": {
                            "type": "boolean",
                            "description": "If true and options provided, allows multiple selections. Default: false.",
                        },
                    },
                    "required": ["id", "header", "question"],
                },
            },
        },
        "required": ["questions"],
    },
}


def _handle_request_user_input(args: dict[str, Any], **kwargs: Any) -> str:
    """Publish an interactive form request and return structured answers."""
    try:
        questions_raw = args.get("questions")
        if not isinstance(questions_raw, list) or not questions_raw:
            raise ValueError("questions must be a non-empty list")
        title = args.get("title", "User Input Request")

        provider = kwargs.get("user_input_provider")
        if callable(provider):
            return _provider_user_input(
                provider, title=title, questions_raw=questions_raw
            )

        from .cli_chat_user_input import _ask_user_input
        from .tui.bottom_pane.request_user_input_form import (
            UserInputRequest,
            UserInputQuestion,
            UserInputOption,
        )

        questions = []
        for q in questions_raw:
            opts = None
            if "options" in q and q["options"]:
                opts = [
                    UserInputOption(
                        label=o["label"],
                        description=o.get("description", ""),
                    )
                    for o in q["options"]
                ]
            questions.append(UserInputQuestion(
                id=q["id"],
                header=q["header"],
                question=q["question"],
                options=opts,
                is_secret=q.get("is_secret", False),
                is_multiselect=q.get("is_multiselect", False),
            ))

        request = UserInputRequest(
            item_id="request_user_input",
            thread_id="",
            turn_id="",
            questions=questions,
        )

        task_id = str(kwargs.get("task_id") or "").strip()
        if task_id:
            from .user_input_store import (
                create_user_input_request,
                wait_for_user_input_response,
            )

            pending = create_user_input_request(
                task_id=task_id,
                payload={
                    "title": title,
                    "item_id": request.item_id,
                    "thread_id": request.thread_id,
                    "turn_id": request.turn_id,
                    "questions": questions_raw,
                },
            )
            result = wait_for_user_input_response(
                str(pending["request_id"]),
                timeout_s=float(kwargs.get("timeout_s") or 1200),
            )
            if result is None:
                return json.dumps(
                    {"cancelled": True, "error": "User cancelled the input form."}
                )
            return json.dumps(result, ensure_ascii=False)

        from rich.console import Console

        response = _ask_user_input(Console(), request)
        if response is None:
            return json.dumps({"cancelled": True, "error": "User cancelled the input form."})
        from .cli_chat_user_input import serialize_user_input_response

        return json.dumps(serialize_user_input_response(response), ensure_ascii=False)

    except Exception as e:
        logger.error("request_user_input error: %s: %s", type(e).__name__, e)
        return json.dumps(
            {"error": f"request_user_input failed: {type(e).__name__}: {e}"}
        )


def _provider_user_input(
    provider: Any,
    *,
    title: Any,
    questions_raw: list[Any],
) -> str:
    """Ask for input through an injected provider instead of the store or a PTY.

    The isolation guest passes the runner's channel bridge (plan todo 10): the
    payload is exactly what the store path publishes and the reply is serialized
    exactly like the store response, so the model cannot tell the transports
    apart. No listener is needed because a provider only exists inside a guest,
    where the host store is absent and the terminal is not ours.
    """
    from .cc_backend_trace import agent_trace_context
    scope = agent_trace_context()
    answer = provider(
        {
            **({"context": scope} if scope.get("subagent_id") else {}),
            "title": title,
            "item_id": "request_user_input",
            "thread_id": "",
            "turn_id": "",
            "questions": questions_raw,
        }
    )
    if answer is None:
        return json.dumps({"cancelled": True, "error": "User cancelled the input form."})
    return json.dumps(answer, ensure_ascii=False)


# ---------------------------------------------------------------------------
# browser — real browser automation via the agent-browser CLI (Hermes pattern)
# ---------------------------------------------------------------------------

BROWSER_SCHEMA: dict[str, Any] = {
    "name": "browser",
    "description": (
        "Real browser automation in the KVM guest: open public web pages, inspect "
        "the accessibility tree, click, type, fill forms, select, scroll, and read text. "
        "action='console' evaluates JavaScript in the currently open page, like the page console; "
        "it cannot run host shell commands. File screenshots/PDF are disabled in KVM. "
        "A persistent per-session browser stays open between calls. Recommended flow: "
        "action='open' with a url, then action='snapshot' to get an accessibility tree "
        "with @eN references, then pass those @refs as `target` to click/fill/type/get. "
        "Prefer this over curl whenever you need to see or interact with a page."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "open", "snapshot", "click", "dblclick", "hover", "focus",
                    "type", "fill", "press", "keyboard_type", "select", "check",
                    "uncheck", "scroll", "wait", "get", "is", "screenshot",
                    "pdf", "eval", "console", "back", "forward", "reload", "close",
                ],
                "description": "Browser action to perform.",
            },
            "url": {"type": "string", "description": "URL for action='open'."},
            "target": {
                "type": "string",
                "description": "Element selector or @ref from snapshot (e.g. @e3).",
            },
            "text": {
                "type": "string",
                "description": "Text for type/fill/keyboard_type/eval/console (JavaScript runs only in the open page).",
            },
            "value": {"type": "string", "description": "Value for action='select'."},
            "key": {
                "type": "string",
                "description": "Key for action='press' (Enter, Tab, Control+a).",
            },
            "option": {
                "type": "string",
                "description": (
                    "For action='get': text/html/value/attr/title/url/count/box. "
                    "For action='is': visible/enabled/checked."
                ),
            },
            "path": {"type": "string", "description": "Output path for screenshot/pdf."},
            "full": {"type": "boolean", "description": "Full-page screenshot."},
            "interactive_only": {
                "type": "boolean",
                "description": "Snapshot only interactive elements (-i).",
            },
            "all": {
                "type": "boolean",
                "description": "For action='close': close every browser session.",
            },
            "direction": {
                "type": "string",
                "description": "Scroll direction: up/down/left/right (default down).",
            },
            "pixels": {"type": "integer", "description": "Scroll pixels (default 500)."},
            "timeout_ms": {
                "type": "integer",
                "description": "Command timeout in ms (default 120000).",
            },
        },
        "required": ["action"],
    },
}


def _browser_session_name(current_session: str | None) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", str(current_session or "default")).strip("-")
    digest = hashlib.sha256((base or "default").encode("utf-8")).hexdigest()[:16]
    return f"i-{digest}"


def _browser_subprocess(argv: list[str], source_env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Run Chrome directly in the guest; the VM is the browser boundary."""
    if source_env.get("IGOREK_ISOLATION_GUEST", "").strip().lower() not in {"1", "true", "yes", "on", "kvm"}:
        return argv, source_env
    env_keys = (
        "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TERM",
        "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "TMPDIR",
        "AGENT_BROWSER_SESSION", "AGENT_BROWSER_NAMESPACE", "AGENT_BROWSER_SOCKET_DIR",
    )
    safe_env = {key: source_env[key] for key in env_keys if key in source_env}
    safe_env.setdefault("HOME", "/home/agent")
    safe_env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    safe_env["IGOREK_ISOLATION_GUEST"] = "1"
    return argv, safe_env


def _browser_output_process(argv, env, *, action, workdir, filesystem_mode, timeout_s):
    root = Path(workdir).expanduser().resolve(strict=True)
    index = argv.index(action) + 1
    raw = Path(argv[index]).expanduser()
    raw = raw if raw.is_absolute() else root / raw
    destination = Path(_validate_write_path(str(raw), str(root), filesystem_mode))
    with private_io_directory(root) as private:
        captured = private / ('capture.pdf' if action == 'pdf' else 'capture.png')
        argv = list(argv)
        argv[index] = str(captured)
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s,
                                env=env, cwd=str(root))
        if result.returncode == 0:
            with open_regular_file(captured) as stream:
                data = stream.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError('Browser output exceeds the 64 MiB file limit')
            authorized = _validate_write_path(str(destination), str(root), filesystem_mode)
            atomic_write_file(authorized, data)
            result.stdout = (result.stdout or '').replace(str(captured), str(destination))
            if str(destination) not in result.stdout:
                result.stdout += f'\nSaved file: {destination}'
        result.stderr = (result.stderr or '').replace(str(captured), str(destination))
        return result


def _handle_browser(args: dict[str, Any], **kwargs: Any) -> str:
    action = str(args.get("action") or "").strip().lower()
    if action == "console":
        action = "eval"
    if not action:
        return tool_error("action is required")
    exe = _agent_browser_path()
    if not exe:
        return tool_error("agent-browser CLI not found; install it or add it to PATH")

    session = _browser_session_name(kwargs.get("current_session"))
    argv: list[str] = [exe, "--session", session]
    target = str(args.get("target") or "").strip()
    text = args.get("text")
    value = args.get("value")
    guest = os.environ.get('IGOREK_ISOLATION_GUEST', '').strip().lower() in {'1', 'true', 'yes', 'on', 'kvm'}
    capture_output = action in {'screenshot', 'pdf'} and not guest

    if action == "open":
        url = str(args.get("url") or "").strip()
        if not url:
            return tool_error("url is required for action='open'")
        argv += ["open", url]
    elif action == "snapshot":
        argv.append("snapshot")
        if args.get("interactive_only"):
            argv.append("-i")
    elif action in ("click", "dblclick", "hover", "focus", "check", "uncheck"):
        if not target:
            return tool_error(f"target is required for action='{action}'")
        argv += [action, target]
    elif action in ("type", "fill"):
        if not target:
            return tool_error(f"target is required for action='{action}'")
        argv += [action, target, str(text if text is not None else "")]
    elif action == "keyboard_type":
        argv += ["keyboard", "type", str(text if text is not None else "")]
    elif action == "press":
        key = str(args.get("key") or text or "").strip()
        if not key:
            return tool_error("key is required for action='press'")
        argv += ["press", key]
    elif action == "select":
        if not target:
            return tool_error("target is required for action='select'")
        argv += ["select", target, str(value if value is not None else text or "")]
    elif action == "scroll":
        direction = str(args.get("direction") or "down").strip() or "down"
        px = args.get("pixels")
        argv += ["scroll", direction] + ([str(int(px))] if px else [])
    elif action == "wait":
        argv += ["wait", target or str(args.get("timeout_ms") or 2000)]
    elif action == "get":
        what = str(args.get("option") or "text").strip()
        argv += ["get", what] + ([target] if target else [])
    elif action == "is":
        what = str(args.get("option") or "visible").strip()
        if not target:
            return tool_error("target is required for action='is'")
        argv += ["is", what, target]
    elif action == "screenshot":
        argv.append("screenshot")
        if args.get("path"):
            argv.append(str(args["path"]))
        elif capture_output:
            argv.append(f'screenshot-{time.time_ns()}.png')
        if args.get("full"):
            argv.append("--full")
    elif action == "pdf":
        out_path = str(args.get("path") or "").strip()
        if not out_path:
            return tool_error("path is required for action='pdf'")
        argv += ["pdf", out_path]
    elif action == "eval":
        if text is None:
            return tool_error("text is required for action='eval'")
        argv += ["eval", str(text)]
    elif action in ("back", "forward", "reload"):
        argv.append(action)
    elif action == "close":
        argv.append("close")
        if args.get("all"):
            argv.append("--all")
    else:
        return tool_error(f"unsupported action: {action}")

    timeout_s = max(5.0, float(args.get("timeout_ms") or 120000) / 1000.0)
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(exe) + os.pathsep + env.get("PATH", "")
    if env.get("IGOREK_ISOLATION_GUEST", "").strip().lower() in {"1", "true", "yes", "on", "kvm"}:
        # agent-browser includes its session name in the Unix socket path.
        # Keep the private per-session directory short enough for Linux sun_path.
        runtime_dir = Path(env.get("HOME", "/home/agent")) / ".r" / session
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runtime_dir, 0o700)
        xdg_dirs = {name: runtime_dir / name.removeprefix("XDG_").removesuffix("_HOME").lower() for name in (
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"
        )}
        for name, path in xdg_dirs.items():
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)
            env[name] = str(path)
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)
        env["AGENT_BROWSER_SESSION"] = session
        env["AGENT_BROWSER_NAMESPACE"] = session
        env["AGENT_BROWSER_SOCKET_DIR"] = str(runtime_dir)
    try:
        argv, env = _browser_subprocess(argv, env)
        if capture_output:
            proc = _browser_output_process(argv, env, action=action,
                workdir=str(kwargs.get('workdir') or os.getcwd()),
                filesystem_mode=str(kwargs.get('filesystem_mode') or 'workspace'), timeout_s=timeout_s)
        else:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                cwd=kwargs.get("workdir") or None,
            )
    except subprocess.TimeoutExpired:
        return tool_error(f"browser action '{action}' timed out after {timeout_s:.0f}s")
    except OSError as exc:
        return tool_error(f"browser action '{action}' failed to start: {exc}")
    except ValueError as exc:
        return tool_error(f"browser output was denied: {exc}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return tool_error(
            f"browser action '{action}' failed (exit {proc.returncode})",
            stderr=err[-4000:],
            stdout=out[-2000:],
        )
    result_text = out or err or "ok"
    if len(result_text) > 20000:
        result_text = result_text[:20000] + "\n...[truncated]"
    return result_text


def _check_browser_tools() -> bool:
    return browser_tools_available()


# ---------------------------------------------------------------------------
# Registration — each tool registers itself at import time
# ---------------------------------------------------------------------------


def _check_web_tools() -> bool:
    return web_tools_available()


def _check_web_extract() -> bool:
    from .web_tools_runtime import active_web_tools
    provider = active_web_tools()
    return web_tools_available() and (provider is None or getattr(provider, "supports_extract", lambda: True)())


def _handle_send_apk(args, **kwargs):
    from .isolation.apk_delivery import send_apk_from_guest
    try:
        return send_apk_from_guest(args["file_path"], kwargs["workdir"])
    except (ValueError, OSError, KeyError):
        return json.dumps({"ok": False, "error": "apk_delivery_refused"})


def _handle_vault_store(args, **kwargs):
    from .vault.agent_values import validate_request
    try:
        validate_request(args)
        provider = kwargs.get('vault_store_provider')
        if not callable(provider):
            return json.dumps({'ok': False, 'error': 'vault_store_unavailable'})
        return provider(args)
    except Exception:
        return json.dumps({'ok': False, 'error': 'vault_store_refused'})


registry.register(
    name='VaultStore', toolset='file', check_fn=_check_file_tools,
    schema={'name': 'VaultStore', 'description': 'Save a new secret or variable from an existing UTF-8 file in the task workspace to the owner Vault. Use a relative source_path. File contents are stored verbatim, up to 1024 characters. The source file is retained. Never pass or print the value itself. Existing records cannot be overwritten, read or granted to consumers.',
            'input_schema': {'type': 'object', 'properties': {'source_path': {'type': 'string'}, 'name': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['secret', 'variable']}}, 'required': ['source_path', 'name', 'kind'], 'additionalProperties': False}},
    handler=_handle_vault_store,
)


registry.register(
    name="SendApk", toolset="file", check_fn=_check_file_tools,
    schema={"name": "SendApk", "description": "Deliver a built Igorek APK from android/app/build/outputs/apk to the owner's fixed Telegram chat through the host broker. No bot token or recipient is accepted.", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"], "additionalProperties": False}},
    handler=_handle_send_apk,
)

registry.register(
    name="Write",
    toolset="file",
    schema=WRITE_SCHEMA,
    handler=_handle_write,
    check_fn=_check_file_tools,
    description="Writes a file to the local filesystem.",
)

registry.register(
    name="Read",
    toolset="file",
    schema=READ_SCHEMA,
    handler=_handle_read,
    check_fn=_check_file_tools,
    description="Reads a file from the local filesystem.",
)

registry.register(
    name="Edit",
    toolset="file",
    schema=EDIT_SCHEMA,
    handler=_handle_edit,
    check_fn=_check_file_tools,
    description="Performs exact string replacements in files.",
)

registry.register(
    name="Bash",
    toolset="terminal",
    schema=BASH_SCHEMA,
    handler=_handle_bash,
    check_fn=_check_file_tools,
    description="Executes a bash command in a shell.",
)

registry.register(
    name="Glob",
    toolset="search",
    schema=GLOB_SCHEMA,
    handler=_handle_glob,
    check_fn=_check_file_tools,
    description="Fast file pattern matching tool.",
)

registry.register(
    name="Grep",
    toolset="search",
    schema=GREP_SCHEMA,
    handler=_handle_grep,
    check_fn=_check_file_tools,
    description="Search file contents with regex patterns.",
)

registry.register(
    name="SessionSearch",
    toolset="session",
    schema=SESSION_SEARCH_SCHEMA,
    handler=_handle_session_search,
    description="Search past Telegram/TUI sessions.",
)

registry.register(
    name="memory_forget",
    toolset="session",
    schema=MEMORY_FORGET_SCHEMA,
    handler=_handle_memory_forget,
    description="Удаляет один явно указанный пользователем факт из долговременной памяти.",
)

registry.register(
    name="update_plan",
    toolset="session",
    schema=UPDATE_PLAN_SCHEMA,
    handler=_handle_update_plan,
    description="Updates the task plan.",
)

registry.register(
    name="web_search",
    toolset="web",
    schema=WEB_SEARCH_SCHEMA,
    handler=_handle_web_search,
    check_fn=_check_web_tools,
    requires_env=["TAVILY_API_KEY"],
    description="Search the web for current information.",
)

registry.register(
    name="web_extract",
    toolset="web",
    schema=WEB_EXTRACT_SCHEMA,
    handler=_handle_web_extract,
    check_fn=_check_web_extract,
    requires_env=["TAVILY_API_KEY"],
    description="Fetch and extract content from URLs.",
)

registry.register(
    name="request_user_input",
    toolset="interactive",
    schema=REQUEST_USER_INPUT_SCHEMA,
    handler=_handle_request_user_input,
    description="Show interactive form and return structured answers.",
)

registry.register(
    name="suggest_enable_mcp",
    toolset="mcp",
    schema=SUGGEST_ENABLE_MCP_SCHEMA,
    handler=_handle_suggest_enable_mcp,
    description="Record a structured suggestion to enable a disabled MCP server.",
)

registry.register(
    name="browser",
    toolset="browser",
    schema=BROWSER_SCHEMA,
    handler=_handle_browser,
    check_fn=_check_browser_tools,
    description="Automate a real browser (open, snapshot, click, type, screenshot).",
)

# ---------------------------------------------------------------------------
# Public API — thin wrappers around the registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MCP bridge — discover and register MCP server tools at import time
# ---------------------------------------------------------------------------


def _init_mcp_bridge() -> None:
    """Start MCP servers and register their tools, if configured."""
    import sys

    # DEBUG: print MCP_SERVERS value
    _mcp_servers = os.environ.get("MCP_SERVERS", "")
    print(f"[cc_backend_tools] START: MCP_SERVERS={_mcp_servers!r}", file=sys.stderr)
    try:
        from .mcp_bridge import discover_and_register_mcp_tools

        registered = discover_and_register_mcp_tools()
        if registered:
            print(
                f"[cc_backend_tools] MCP tools registered: {', '.join(registered)}",
                file=sys.stderr,
            )
        else:
            print(f"[cc_backend_tools] MCP: no tools registered", file=sys.stderr)
    except Exception as exc:
        print(f"[cc_backend_tools] MCP bridge init FAILED: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)


_init_mcp_bridge()


# Schedule tools registration (trigger import + registry.register calls)
try:
    from . import schedule_tool as _schedule_tool  # noqa: F401
except Exception as _exc:
    print(f"[cc_backend_tools] Schedule tools init skipped: {_exc}", file=sys.stderr)

# Delegate tool registration
try:
    from . import delegate_tool as _delegate_tool  # noqa: F401
except Exception as _exc:
    print(f"[cc_backend_tools] Delegate tool init skipped: {_exc}", file=sys.stderr)

# Skills tools registration
try:
    from . import skills_tool as _skills_tool  # noqa: F401
except Exception as _exc:
    print(f"[cc_backend_tools] Skills tools init skipped: {_exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public API — thin wrappers around the registry
# ---------------------------------------------------------------------------


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return Anthropic-format tool definitions for the API request.

    Delegates to the registry which filters by check_fn availability.
    """
    definitions = registry.get_definitions()
    if sys.platform == 'win32':
        return [item for item in definitions if item.get('name', '').lower() != 'bash']
    return definitions


DANGEROUS_TOOLS = {"write", "edit", "bash"}
DANGEROUS_BASH_PATTERNS = (
    r"rm\s+-",
    r"\bdd\s+",
    r"mkfs",
    # Redirect into a device node is destructive; /dev/null is the benign
    # everyday idiom and must not trigger approval (negative lookahead).
    r">\s*/dev/(?!null\b)",
    r"chmod\s+777",
    r"chown\s+-R\s+root",
    r":\(\)\{:\|:&\};:",
    r"wget.*\|.*sh",
    r"curl.*\|.*sh",
)


def needs_approval(tool_name: str, input_data: dict[str, Any]) -> bool:
    """Check if a tool requires user approval before execution.

    Write/Edit stay approval-gated at the policy level; Telegram and
    autonomous tasks bypass it via ``metadata["auto_approve"]`` (see
    ``_approved_tool_call``), not by globally disabling the guard. Benign bash
    such as ``2>/dev/null`` does not require approval.
    """
    tool_name_lower = tool_name.lower()
    if tool_name_lower in {'calendar_create_event','calendar_move_event'}:
        return True

    if tool_name_lower in DANGEROUS_TOOLS:
        if tool_name_lower == "bash":
            cmd = input_data.get("command", "")
            # The exact sudo wrapper is a privileged operation. Route it through
            # the ordinary tool-approval flow so mobile approval resumes this
            # same guest task before _tool_bash invokes sudo inside its VM.
            if _sudo_broker_command(cmd) is not None:
                return True
            for pattern in DANGEROUS_BASH_PATTERNS:
                import re
                if re.search(pattern, cmd, re.IGNORECASE):
                    return True
            return False
        return True

    return False


def execute_tool(
    name: str,
    input_data: dict[str, Any],
    workdir: str,
    filesystem_mode: str = "workspace",
    guard_mode: str | None = None,
    checkpoint_manager: CheckpointManager | None = None,
    current_session: str | None = None,
    source_platform: str | None = None,
    source_chat_id: str | None = None,
    approval_callback=None,
    **kwargs: Any,
) -> str:
    """Execute a single tool call and return the result string.

    Delegates to the registry dispatch after coercing argument types.
    Extra kwargs (delegate_depth, iteration_budget, etc.) are forwarded
    so that delegate_task can access runtime context.

    If approval_callback is provided and returns False for dangerous tools,
    returns special APPROVAL_REQUIRED result instead of executing.
    """
    from .coerce_tools import coerce_tool_args

    metadata = kwargs.get('delegate_metadata') or {}
    if metadata.get('demo_workspace'):
        from .mobile_bridge.demo import SAFE_TOOLS
        if name not in SAFE_TOOLS:
            raise ValueError('Only demo tools are available in this workspace')
        filesystem_mode = 'workspace'
        input_data = dict(input_data)
        path_key = 'file_path' if name in {'Write', 'Read', 'Edit'} else 'path'
        if name != 'update_plan':
            target = os.path.realpath(os.path.join(workdir, str(input_data.get(path_key) or '.')))
            if not _is_within_root(target, os.path.realpath(workdir)):
                raise ValueError('Demo tools cannot access files outside their workspace')
            input_data[path_key] = target
    input_data = coerce_tool_args(name, input_data)
    return registry.dispatch(
        name,
        input_data,
        workdir=workdir,
        filesystem_mode=filesystem_mode,
        guard_mode=guard_mode,
        checkpoint_manager=checkpoint_manager,
        current_session=current_session,
        source_platform=source_platform,
        source_chat_id=source_chat_id,
        **kwargs,
    )

# Register the declarative, read-only chart tool for all model providers.
from . import chart_tool as _chart_tool  # noqa: F401,E402
