from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..core.opencode_quota import (
    CACHE_LOCK as OPENCODE_CACHE_LOCK,
    KEY_CACHE,
    OPENCODE_KEY_TTL_S,
    OPENCODE_SERVICES,
    OPENCODE_USAGE_CACHE_S,
    OPENCODE_USAGE_TIMEOUT_S,
    OPENCODE_USAGE_URL,
    OPENCODE_USAGE_WINDOWS,
    OPENCODE_USER_AGENT,
    USAGE_CACHE,
    OpenCodeUsageError,
    fetch_opencode_usage,
    opencode_api_key,
)
from ..core.mobile_bridge.uploads import MAX_UPLOAD_BYTES
from ..core.runtime import DEFAULT_CLI_HOME, get_chat_sessions_dir, get_profile_path, profile_display_model_name
from ..core.isolation.terminal_manager import (
    TICKET_TTL_S as TERMINAL_TICKET_TTL_S,
    PtyTerminalBackend,
    TerminalError,
    TerminalManager,
)
from .artifact_preview import MAX_PREVIEW_HTML_BYTES, prepare_html_preview
from .terminal_endpoint import TerminalEndpoint, TerminalLog
from ..core.isolation.terminal_vsock import RoutedTerminalBackend
from .websocket import (
    DEFAULT_MAX_MESSAGE_BYTES as WS_MAX_MESSAGE_BYTES,
    HandshakeRequest,
    WebSocket,
    WebSocketError,
    handshake_headers,
    validate_handshake,
)

_COOKIE = "__Host-igorek-device"
_STATIC = Path(__file__).with_name("static")
_STATIC_TYPES = {
    "diagnostic-support.js": "text/javascript; charset=utf-8",
    "diagnostic-support.css": "text/css; charset=utf-8",
    "error-catalog.js": "text/javascript; charset=utf-8",
    "error-diagnostics.js": "text/javascript; charset=utf-8",
    "completion-sound.js": "text/javascript; charset=utf-8",
    "alarm1.mp3": "audio/mpeg",
    "audio/task-completed.mp3": "audio/mpeg",
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "github-reviews.js": "text/javascript; charset=utf-8",
    "session-summary.js": "text/javascript; charset=utf-8",
    "app-history.js": "text/javascript; charset=utf-8",
    "media-previews.js": "text/javascript; charset=utf-8",
    "downloads.js": "text/javascript; charset=utf-8",
    "workspace-tabs.js": "text/javascript; charset=utf-8",
    "opencode-panel.js": "text/javascript; charset=utf-8",
    "tool-groups.js": "text/javascript; charset=utf-8",
    "activity.css": "text/css; charset=utf-8",
    "mcp-task.js": "text/javascript; charset=utf-8",
    "plugins.js": "text/javascript; charset=utf-8",
    "session-agent.js": "text/javascript; charset=utf-8",
    "composer-profiles.js": "text/javascript; charset=utf-8",
    "changes.js": "text/javascript; charset=utf-8",
    "voice.js": "text/javascript; charset=utf-8",
    "render.js": "text/javascript; charset=utf-8",
    "file-viewer.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "mark.svg": "image/svg+xml",
    # Third-party viewer libraries: SheetJS (Excel) and highlight.js.
    "vendor/xlsx.full.min.js": "text/javascript; charset=utf-8",
    "vendor/highlight.min.js": "text/javascript; charset=utf-8",
    "vendor/XLSX-LICENSE.txt": "text/plain; charset=utf-8",
    "vendor/HIGHLIGHT-LICENSE.txt": "text/plain; charset=utf-8",
    "vendor/README.txt": "text/plain; charset=utf-8",
    # Third-party terminal emulator: xterm.js and its CSS.
    "vendor/xterm.js": "text/javascript; charset=utf-8",
    "vendor/xterm.css": "text/css; charset=utf-8",
    "vendor/addon-fit.js": "text/javascript; charset=utf-8",
    "vendor/XTERM-LICENSE.txt": "text/plain; charset=utf-8",
    # Client-side implementation of the 'Terminal' tab.
    "terminal.js": "text/javascript; charset=utf-8",
    # The Excel parsing worker connects only from its own origin.
    "workers/xlsx-worker.js": "text/javascript; charset=utf-8",
}
_MAX_BODY = 256_000
# Nested payloads exceed the JSON limit: the request body consists of raw file bytes, and the boundary
# is set to the same limit as that of the mobile bridge.
_MAX_UPLOAD = int(MAX_UPLOAD_BYTES)
_MAX_UPLOAD_HEADER = 1024
# Artifact preview (?preview=1) is served as a standalone document. The interface
# CSP is inherited by srcdoc documents, which blocks their inline styles and
# scripts; here the document gets its own sandboxed CSP instead: inline allowed,
# opaque origin (sandbox without allow-same-origin), no network and no cookies.
# Known pinned libraries are embedded locally as data scripts; no CDN is contacted.
# HTML/SVG pages cannot reach the interface or fetch external assets.
_PREVIEW_CSP = (
    "sandbox allow-scripts; default-src 'none'; base-uri 'none'; form-action 'none'; "
    "style-src 'unsafe-inline'; script-src 'unsafe-inline' data:; img-src data: blob:; "
    "font-src data:; media-src data: blob:; frame-ancestors 'self'"
)
_PREVIEW_CHARSET_TYPES = {"text/html", "image/svg+xml", "application/xhtml+xml", "text/plain"}
_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_TASK_ID = _SESSION_ID
# The profile name comes from the URL and is converted into a config file name: only a safe
# character set is used to prevent any directory traversal attacks.
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Providers for which the cache of `models.dev` returns a context window; order follows that in
# factory/core/cc_backend_executor.py::_context_window_from_models_cache.
_CONTEXT_WINDOW_PROVIDERS = ("opencode-go", "opencode", "openrouter", "minimax", "glm")
# External agent history: browser access is restricted to built-in sources, and the identifier
# remains in a safe character set (UUID, ses_*, rollout-id).
_EXTERNAL_SOURCES = ("claude", "opencode", "codex")
_EXTERNAL_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
# History scanning reads large jsonl/sqlite files, so these routes have a separate, more generous timeout
# compared to standard proxies.
_EXTERNAL_TIMEOUT_S = 60.0
_APPROVAL_DECISIONS = {"allow_once", "allow_session", "reject"}
_CANCEL_MODES = {"cancel", "stop", "kill"}
# One-time connection code lasts 300 seconds; browser brute-force attempts are limited to the same window.
# Failed attempts are counted, and a 429 response is returned upon exhaustion.
_PAIR_WINDOW_S = 300.0
_PAIR_MAX_FAILURES = 10
# Service logs: the browser is only granted access to a fixed set of systemd units.
# Key -> unit; arbitrary commands and units from outside are not accepted.
_LOG_UNITS = {
    "gateway": "igorek-gateway.service",
    "mobile": "igorek-mobile.service",
    "web": "igorek-web-ui.service",
}
_LOG_LINES_DEFAULT = 200
_LOG_LINES_MAX = 500
_LOG_TIMEOUT_S = 10.0
_WORKER_UNIT = "igorek-worker@{profile}.service"
_WORKER_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Same values that disable additional profiles in the gateway stack.
_WORKER_PROFILES_OFF = {"off", "0", "no", "false", "disabled", "none"}
# The composer reference menu: file listings occur only within the session working directory.
# Absolute paths, `..`, and external symlinks are rejected; system directories are hidden, and response volume is limited.
_MENTION_HIDDEN_DIRS = frozenset({
    ".git", ".checkpoints", ".tool_results", ".codex", "__pycache__",
    "node_modules", ".venv", ".igorek-host-data", ".igorek-artifacts",
})
_MENTION_MAX_ENTRIES = 500
_MENTION_MAX_DEPTH = 3
_MENTION_MAX_PATH = 500
# File name search (pathless bridge attachments) is limited by traversal budget.
_MENTION_SCAN_LIMIT = 5000
# 'Open in folder': the desktop opener runs briefly without a shell.
_REVEAL_TIMEOUT_S = 5.0
_REVEAL_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")
# Durable session logs (agent plan, usage) are read in pages; the bridge’s `list_events` is capped at 500 events per request (store._MAX_EVENT_LIMIT).
_EVENT_PAGE_DEFAULT = 500
_EVENT_PAGE_MAX = 500
# The OpenCode Go quota implementation is located in factory/core/opencode_quota.py — a single
# source for the web panel and mobile bridge (shared response cache of 60 seconds and a shared
# key resolution via Igorek Vault). The key does not leave the host and is not included in
# API responses to browsers or mobile devices.
_OPENCODE_USAGE_URL = OPENCODE_USAGE_URL
_OPENCODE_USER_AGENT = OPENCODE_USER_AGENT
_OPENCODE_USAGE_TIMEOUT_S = OPENCODE_USAGE_TIMEOUT_S
_OPENCODE_USAGE_CACHE_S = OPENCODE_USAGE_CACHE_S
_OPENCODE_KEY_TTL_S = OPENCODE_KEY_TTL_S
_OPENCODE_SERVICES = OPENCODE_SERVICES
_OPENCODE_USAGE_WINDOWS = OPENCODE_USAGE_WINDOWS
_OPENCODE_CACHE_LOCK = OPENCODE_CACHE_LOCK
_OPENCODE_USAGE_CACHE = USAGE_CACHE
_OPENCODE_KEY_CACHE = KEY_CACHE
_OpenCodeUsageError = OpenCodeUsageError
_opencode_api_key = opencode_api_key
_fetch_opencode_usage = fetch_opencode_usage


def _log_unit_for_key(key: str) -> str:
    """Allowed request key -> systemd service unit name ('' — key not allowed)."""
    value = str(key or "").strip()
    if value in _LOG_UNITS:
        return _LOG_UNITS[value]
    prefix = "worker:"
    if value.startswith(prefix):
        profile = value[len(prefix):]
        if _WORKER_PROFILE_RE.fullmatch(profile):
            return _WORKER_UNIT.format(profile=profile)
    return ""


def _preview_content_type(value: str) -> str:
    """Document type for preview: adds charset to text artifacts."""
    raw = str(value or "")
    base = raw.split(";")[0].strip().lower()
    if "charset=" in raw.lower() or base not in _PREVIEW_CHARSET_TYPES:
        return raw or "application/octet-stream"
    return f"{base}; charset=utf-8"


# Rough KVM policy boundary for the workspace folder: isolation does not propagate
# the home directory or the root of the file system (factory/core/isolation/workspace.py,
# _sensitive_reason: home and root, HOME_DENIED_PATHS). Full policy details are not repeated here —
# standard project directories should pass through.
_CWD_DENIED_MESSAGES = {
    "home": "Рабочая папка не может быть домашней папкой: KVM-изоляция её не пробрасывает. Выберите папку проекта.",
    "root": "Рабочая папка не может быть корнем файловой системы. Выберите папку проекта.",
}


def _cwd_denied_reason(raw: str) -> str:
    """Reason for refusal of cwd ('' — path is valid): home, root, or filesystem root."""
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        resolved = Path(value).expanduser().resolve()
    except (OSError, RuntimeError):
        return ""
    if resolved == Path(resolved.anchor):
        return "root"
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return ""
    if resolved == home:
        return "home"
    return ""


def _run_command(args: list[str], timeout: float = _LOG_TIMEOUT_S) -> subprocess.CompletedProcess[str] | None:
    """Execution of allowlisted command without shell; None — command unavailable or stuck."""
    try:
        return subprocess.run(
            args,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _journal_lines(unit: str, lines: int) -> tuple[list[str], bool, str]:
    """Last unit log lines: (number of lines, whether older entries exist, error present)."""
    # Requesting an extra line to distinguish between 'exactly lines' and 'journal
    # longer than expected' and correctly return the truncated flag.
    result = _run_command([
        "journalctl", "--user", "-u", unit, "-n", str(int(lines) + 1),
        "--no-pager", "--output=short-iso",
    ])
    if result is None:
        return [], False, "journalctl недоступен"
    output = str(result.stdout or "")
    if result.returncode != 0 and not output.strip():
        detail = str(result.stderr or "").strip().splitlines()
        return [], False, detail[0] if detail else f"journalctl завершился с кодом {result.returncode}"
    # '-- No entries --' is a placeholder for journalctl, not a journal line.
    rows = [line for line in output.splitlines() if line and line.strip() != "-- No entries --"]
    truncated = len(rows) > lines
    return rows[-lines:], truncated, ""


def _worker_profile_from_unit(name: str) -> str:
    """Profile from unit name `igorek-worker@<profile>.service` ('' — not this profile)."""
    value = str(name or "").strip()
    head, tail = "igorek-worker@", ".service"
    if not value.startswith(head) or not value.endswith(tail):
        return ""
    profile = value[len(head):-len(tail)]
    # The template igorek-worker@.service is not a specific worker.
    if not profile or not _WORKER_PROFILE_RE.fullmatch(profile):
        return ""
    return profile


def _worker_profiles() -> list[str]:
    """Actual worker profiles configured on the machine.

    Sources: loaded systemd units, installed unit files, and explicit
    `FACTORY_WORKER_PROFILES` list (read by the gateway stack). If nothing is
    found, the runtime active profile worker is displayed.
    """
    profiles: list[str] = []

    def add(profile: str) -> None:
        value = str(profile or "").strip()
        if value and value not in profiles:
            profiles.append(value)

    for args in (
        ["systemctl", "--user", "list-units", "igorek-worker@*.service", "--all", "--type=service", "--no-legend", "--plain"],
        ["systemctl", "--user", "list-unit-files", "igorek-worker@*.service", "--type=service", "--no-legend", "--plain"],
    ):
        result = _run_command(args)
        if result is None or result.returncode != 0:
            continue
        for line in str(result.stdout or "").splitlines():
            fields = line.split()
            if fields:
                add(_worker_profile_from_unit(fields[0]))
    extra = str(os.environ.get("FACTORY_WORKER_PROFILES") or "").strip()
    if extra.lower() not in _WORKER_PROFILES_OFF:
        for item in extra.split(","):
            candidate = item.strip()
            if candidate and _WORKER_PROFILE_RE.fullmatch(candidate):
                add(candidate)
    if not profiles:
        add(str(os.environ.get("FACTORY_ACTIVE_PROFILE") or "").strip())
    return profiles


def _profile_model(profile: str) -> str:
    """Current local server model, or configured cloud model when available."""
    name = str(profile or "").strip()
    if not name or not _PROFILE_NAME.fullmatch(name):
        return ""
    try:
        data = json.loads(get_profile_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    model = data.get("model") if isinstance(data, dict) else ""
    if not isinstance(model, str) or not model.strip():
        return ""
    return profile_display_model_name(data).strip()


def _model_context_window(model: str) -> int | None:
    """Model context window from cache `models.dev` (None — unknown)."""
    name = str(model or "").strip()
    if not name:
        return None
    try:
        from ..core.opencode_models import get_context_window
    except Exception:
        return None
    for provider_id in _CONTEXT_WINDOW_PROVIDERS:
        try:
            found = get_context_window(name, provider_id=provider_id)
        except Exception:
            found = None
        if found:
            return int(found)
    try:
        from ..core.openrouter_pricing import get_openrouter_context_window
        found = get_openrouter_context_window(name)
    except Exception:
        found = None
    return int(found) if found else None



def _profile_context_window(profile: str, model: str) -> int | None:
    """Prefer the live configured limit over a catalogue's training maximum."""
    name = str(profile or '').strip()
    if name and _PROFILE_NAME.fullmatch(name):
        try:
            config = json.loads(get_profile_path(name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            config = {}
        if isinstance(config, dict):
            from ..core.local_model_context import is_loopback_model_url, local_model_context_window
            base = config.get('base_url')
            if isinstance(base, str) and is_loopback_model_url(base):
                return local_model_context_window(base)
    return _model_context_window(model)


def _parse_systemctl_show(output: str) -> dict[str, dict[str, str]]:
    """Parsing `systemctl show` for multiple units (blocks separated by an empty line)."""
    units: dict[str, dict[str, str]] = {}
    block: dict[str, str] = {}
    unit_id = ""
    for line in [*output.splitlines(), ""]:
        if not line.strip():
            if unit_id and block:
                units[unit_id] = block
            block, unit_id = {}, ""
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name == "Id":
            unit_id = value.strip()
        if name:
            block[name] = value.strip()
    return units


def _units_status() -> tuple[list[dict[str, str]], str]:
    """State of allowed units: (records, error).

    A single call to `systemctl show` for the entire set. An error does not invalidate records:
    UI will display 'unknown' instead of a crash.
    """
    entries = list(_LOG_UNITS.items())
    for profile in _worker_profiles():
        entries.append((f"worker:{profile}", _WORKER_UNIT.format(profile=profile)))
    names = [unit for _, unit in entries]
    states: dict[str, dict[str, str]] = {}
    error = ""
    result = _run_command([
        "systemctl", "--user", "show", "-p", "Id", "-p", "LoadState",
        "-p", "ActiveState", "-p", "ActiveEnterTimestamp", "--no-pager", *names,
    ])
    if result is None:
        error = "systemctl недоступен"
    elif result.returncode != 0:
        detail = str(result.stderr or "").strip().splitlines()
        error = detail[0] if detail else f"systemctl завершился с кодом {result.returncode}"
    else:
        states = _parse_systemctl_show(str(result.stdout or ""))
    units: list[dict[str, str]] = []
    for key, unit in entries:
        state = states.get(unit, {})
        if not state:
            active = "unknown"
        elif state.get("LoadState") == "not-found":
            active = "not-found"
        else:
            active = state.get("ActiveState") or "unknown"
        units.append({
            "key": key, "unit": unit, "active": active,
            "since": state.get("ActiveEnterTimestamp", ""),
        })
    return units, error


def _session_cwd(session_id: str) -> str:
    """Session working directory from meta header in its jsonl file ('' — no session or cwd).

    The same format as read by the mobile bridge: the first non-empty line of the file is a record with ``type=meta`` and a ``cwd`` field. The web interface operates on the same host and data root as the bridge, so meta data is read directly.
    """
    path = Path(get_chat_sessions_dir()) / f"{session_id}.jsonl"
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if isinstance(entry, dict) and entry.get("type") == "meta":
                    cwd = entry.get("cwd")
                    return cwd if isinstance(cwd, str) else ""
                return ""
    except (OSError, json.JSONDecodeError):
        return ""
    return ""


def _mention_tree_root(session_id: str) -> Path:
    """Actual path of the session working directory; OSError if the directory does not exist or is inaccessible."""
    raw = _session_cwd(session_id).strip()
    if not raw:
        raise FileNotFoundError("Рабочая папка сессии не задана")
    try:
        root = Path(raw).expanduser().resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError("Рабочая папка сессии недоступна") from exc
    if not root.is_dir():
        raise NotADirectoryError("Рабочая папка сессии недоступна")
    return root


def _mention_target(root: Path, relative: str) -> Path:
    """File or directory inside root via relative path; otherwise ValueError/OSError.

    General safe resolution for paths working with the session working directory:
    absolute paths, ``~``, and ``..`` are rejected; system directories are hidden; external symlinks beyond root are not allowed.
    """
    raw = str(relative or "").strip().replace("\\", "/")
    if raw.startswith("~") or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("Путь должен быть относительным внутри рабочей папки")
    segments = [segment for segment in raw.split("/") if segment not in {"", "."}]
    if any(segment == ".." for segment in segments):
        raise ValueError("Путь не может выходить за пределы рабочей папки")
    if any(segment in _MENTION_HIDDEN_DIRS for segment in segments):
        raise PermissionError("Служебные каталоги скрыты")
    if not segments:
        return root
    candidate = root.joinpath(*segments)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError("Путь не найден") from exc
    if resolved != root and root not in resolved.parents:
        raise PermissionError("Путь выходит за пределы рабочей папки")
    return resolved


def _mention_subdir(root: Path, relative: str) -> Path:
    """Directory inside root via relative path; otherwise ValueError/OSError."""
    try:
        resolved = _mention_target(root, relative)
    except FileNotFoundError as exc:
        raise FileNotFoundError("Каталог не найден") from exc
    if not resolved.is_dir():
        raise NotADirectoryError("Это файл, а не каталог")
    return resolved


def _mention_find_by_name(root: Path, name: str) -> Path:
    """This is the sole file with this name within the root directory (otherwise raises `ValueError`/`FileNotFoundError`).

    Required for files from the attachment list: the bridge only returns the filename without the path.
    Traversal is budget-limited and does not descend into system directories; ambiguous names are intentional errors, not random choices.
    """
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise FileNotFoundError("Файл не найден в рабочей папке")
    matches: list[Path] = []
    scanned = 0
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [item for item in dirnames if item not in _MENTION_HIDDEN_DIRS]
        for filename in filenames:
            scanned += 1
            if scanned > _MENTION_SCAN_LIMIT:
                raise ValueError("Рабочая папка слишком большая для поиска по имени — укажите путь")
            if filename != name:
                continue
            candidate = Path(directory) / filename
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved != root and root not in resolved.parents:
                continue
            matches.append(resolved)
            if len(matches) > 1:
                raise ValueError("В рабочей папке несколько файлов с таким именем — укажите путь")
    if not matches:
        raise FileNotFoundError("Файл не найден в рабочей папке")
    return matches[0]


def _session_display_env() -> dict[str, str]:
    """Graphical session environment for the desktop opener.

    First, variables from the current process are taken: the `systemd` unit inherits the user environment, so a running service already has `DISPLAY` set.
    Missing values are fetched from `systemctl --user show-environment`—the same source, without hardcoding a specific display.
    """
    env = dict(os.environ)
    missing = [key for key in _REVEAL_ENV_KEYS if not str(env.get(key) or "").strip()]
    if not missing:
        return env
    result = _run_command(["systemctl", "--user", "show-environment"])
    if result is None or result.returncode != 0:
        return env
    for line in str(result.stdout or "").splitlines():
        name, _, value = line.partition("=")
        name = name.strip()
        if name in missing and value.strip():
            env[name] = value.strip()
    return env


def _reveal_open(target: Path) -> tuple[str, str, str]:
    """Open the target in the file manager: (opener, error code, error message)."""
    env = _session_display_env()
    if not str(env.get("DISPLAY") or "").strip() and not str(env.get("WAYLAND_DISPLAY") or "").strip():
        return "", "no_display", "Нет графической сессии (DISPLAY/WAYLAND_DISPLAY): файловый менеджер недоступен"
    directory = target if target.is_dir() else target.parent
    candidates: list[list[str]] = [["xdg-open", str(directory)]]
    if target.is_file():
        candidates.append(["nautilus", "--select", str(target)])
    else:
        candidates.append(["nautilus", str(target)])
    candidates.append(["gio", "open", str(directory)])
    errors: list[str] = []
    for args in candidates:
        try:
            result = subprocess.run(
                args,
                shell=False,
                timeout=_REVEAL_TIMEOUT_S,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=env,
            )
        except FileNotFoundError:
            errors.append(f"{args[0]}: не найден")
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{args[0]}: {exc}")
            continue
        if result.returncode == 0:
            return args[0], "", ""
        detail = str(result.stderr or "").strip().splitlines()
        errors.append(f"{args[0]}: {detail[0] if detail else f'код {result.returncode}'}")
    return "", "opener_failed", "; ".join(errors) or "opener недоступен"


def _mention_collect(
    root: Path,
    directory: Path,
    prefix: str,
    level: int,
    depth: int,
    entries: list[dict[str, Any]],
) -> bool:
    """Recursive content collection: `True` if the record limit is reached."""
    try:
        children = list(directory.iterdir())
    except OSError:
        return False
    for child in children:
        if len(entries) >= _MENTION_MAX_ENTRIES:
            return True
        is_dir = child.is_dir()
        if is_dir and child.name in _MENTION_HIDDEN_DIRS:
            continue
        try:
            resolved = child.resolve(strict=True)
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            continue
        if resolved.is_dir():
            kind = "dir"
        elif resolved.is_file():
            kind = "file"
        else:
            continue
        try:
            size = resolved.stat().st_size if kind == "file" else 0
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": f"{prefix}{child.name}",
            "type": kind,
            "size": size,
        })
        if kind == "dir" and level < depth:
            if _mention_collect(root, resolved, f"{prefix}{child.name}/", level + 1, depth, entries):
                return True
    return False


def _mention_entries(root: Path, base: Path, depth: int) -> tuple[list[dict[str, Any]], bool]:
    """Directory entries for `base` (paths relative to root), directories listed first."""
    prefix = "" if base == root else f"{base.relative_to(root).as_posix()}/"
    entries: list[dict[str, Any]] = []
    truncated = _mention_collect(root, base, prefix, 1, depth, entries)
    entries.sort(key=lambda item: (item["type"] != "dir", item["name"].casefold(), item["path"]))
    return entries, truncated


def _bridge_base(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Адрес mobile bridge должен быть локальным http:// или https://")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("В URL mobile bridge нельзя задавать путь, учётные данные или параметры")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if not loopback:
        raise ValueError("Веб-интерфейс принимает только адрес mobile bridge на localhost")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Порт mobile bridge должен быть в диапазоне 1–65535")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _certificate_paths() -> tuple[Path, Path, str]:
    root = (
        os.environ.get("IGOREK_FACTORY_HOME", "").strip()
        or os.environ.get("FACTORY_DATA_DIR", "").strip()
        or str(DEFAULT_CLI_HOME)
    )
    directory = Path(root).expanduser() / "web-ui" / "tls"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    cert_path, key_path = directory / "localhost-cert.pem", directory / "localhost-key.pem"
    if not cert_path.is_file() or not key_path.is_file():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Igorek Local Web")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=397))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256())
        )
        key_path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return cert_path, key_path, cert.fingerprint(hashes.SHA256()).hex()


from ..core.mobile_bridge.tls_server import TLSHandshakeMixin


class _Server(TLSHandshakeMixin, ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, bridge_url: str) -> None:
        self.bridge_url = bridge_url
        self._pair_failures: list[float] = []
        self._pair_lock = threading.Lock()
        # Terminal sessions reside within the web interface process: the PTY belongs to the host,
        # not the bridge, so a bridge restart does not affect them.
        self._terminal_modes = {}
        self.terminals = TerminalManager(backend_factory=lambda: RoutedTerminalBackend(self._terminal_modes))
        self.terminal_log = TerminalLog()
        super().__init__(("127.0.0.1", port), _Handler)

    def pair_retry_after(self) -> int:
        """Seconds until the next connection attempt (0 means attempts are allowed)."""
        now = time.monotonic()
        with self._pair_lock:
            self._pair_failures = [item for item in self._pair_failures if now - item < _PAIR_WINDOW_S]
            if len(self._pair_failures) < _PAIR_MAX_FAILURES:
                return 0
            oldest = self._pair_failures[0]
        return max(1, int(_PAIR_WINDOW_S - (now - oldest)) + 1)

    def record_pair_failure(self) -> None:
        with self._pair_lock:
            self._pair_failures.append(time.monotonic())


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IgorekLocalWeb/1"

    @property
    def bridge_url(self) -> str:
        return str(getattr(self.server, "bridge_url"))

    @property
    def origin(self) -> str:
        return f"https://127.0.0.1:{self.server.server_port}"

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._check_origin(method):
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if method == "GET" and (path == "/" or path.lstrip("/") in _STATIC_TYPES):
            self._static("index.html" if path == "/" else path.lstrip("/"))
            return
        if path == "/api/status" and method == "GET":
            self._status()
            return
        if path == "/api/pair" and method == "POST":
            retry_after = self.server.pair_retry_after()
            if retry_after:
                self._json(
                    429,
                    {"error": f"Слишком много неудачных попыток. Повторите через {retry_after} с."},
                    headers={"Retry-After": str(retry_after)},
                )
                return
            body = self._body()
            if body is not None:
                self._pair(body)
            return
        if path == "/api/logout" and method == "POST":
            body = self._body()
            if body is not None:
                self._logout()
            return
        if path == "/api/mcp" and method == "POST":
            body = self._body()
            if body is not None:
                self._proxy(method, "/mcp", body)
            return
        if path == "/api/session-options" and method == "GET":
            self._proxy(method, "/session-options")
            return
        if path == '/api/diagnostics' and method == 'GET':
            self._proxy(method, '/diagnostics')
            return
        operation_match = re.fullmatch(r'/api/operations/([A-Za-z0-9_-]{8,160})', path)
        if operation_match and method == 'GET':
            self._proxy(method, '/operations/'+operation_match.group(1)+'?'+urllib.parse.urlparse(self.path).query)
            return
        if path == '/api/schedules' and method in {'GET','POST'}:
            body = self._body() if method=='POST' else None
            if method=='GET' or body is not None:self._proxy(method,'/schedules',body)
            return
        schedule_parts = [part for part in path.split('/') if part]
        if len(schedule_parts) in {3,4} and schedule_parts[:2]==['api','schedules']:
            identity=schedule_parts[2]
            if not _SESSION_ID.fullmatch(identity):
                self._json(400,{'error':'Некорректный ID расписания'});return
            target='/schedules/'+urllib.parse.quote(identity,safe='')
            if len(schedule_parts)==3 and method in {'PATCH','DELETE'}:
                body=self._body() if method=='PATCH' else None
                if method=='DELETE' or body is not None:self._proxy(method,target,body)
            elif len(schedule_parts)==4 and schedule_parts[3]=='run' and method=='POST':
                body=self._body()
                if body is not None:self._proxy(method,target+'/run',body)
            else:self._json(404,{'error':'not found'})
            return
        if path == "/api/sessions" and method == "GET":
            query=urllib.parse.parse_qs(parsed.query)
            try:limit=max(1,min(5000,int((query.get('limit') or ['100'])[0])))
            except ValueError:
                self._json(400,{'error':'Некорректный limit'});return
            self._proxy(method, '/sessions?limit='+str(limit))
            return
        if path == "/api/sessions" and method == "POST":
            body = self._body()
            if body is not None:
                self._proxy(method, "/sessions", body)
            return
        if path == "/api/about" and method == "GET":
            self._about()
            return
        if path == "/api/model-info" and method == "GET":
            self._model_info(parsed.query)
            return
        if path == "/api/provider-usage" and method == "GET":
            self._provider_usage(parsed.query)
            return
        if path == "/api/opencode-usage" and method == "GET":
            self._opencode_usage(parsed.query)
            return
        if path == "/api/logs/status" and method == "GET":
            self._logs_status()
            return
        if path == "/api/logs" and method == "GET":
            self._logs(parsed.query)
            return
        if path == "/api/terminal" and method == "GET":
            # Terminal WebSocket: Host/Origin checks are performed here before
            # upgrade. Shell access is confirmed via a one-time ticket,
            # issued only through a connected cookie.
            self._terminal_ws()
            return
        if path in {"/api/approval-settings", "/api/security-settings", "/api/sandbox-settings"} and method in {
            "GET",
            "PATCH",
        }:
            self._proxy_json(method, path[len("/api"):])
            return
        if path == "/api/devices" and method == "GET":
            self._proxy(method, "/devices")
            return
        if path == "/api/devices/purge-revoked" and method == "POST":
            self._proxy(method, "/devices/purge-revoked")
            return
        if path == "/api/extensions" and method == "GET":
            profile = str((urllib.parse.parse_qs(parsed.query).get("profile") or [""])[0]).strip()
            suffix = f"?profile={urllib.parse.quote(profile, safe='')}" if profile else ""
            self._proxy(method, f"/extensions{suffix}")
            return
        if path == "/api/approvals" and method == "GET":
            self._approvals(urllib.parse.parse_qs(parsed.query))
            return
        if path == "/api/voice/config" and method == "GET":
            self._proxy(method, "/voice/config")
            return
        if path == "/api/voice/session" and method == "POST":
            body = self._body()
            if body is not None:
                self._proxy(method, "/voice/session", body)
            return
        if path == "/api/voice/sdp" and method == "POST":
            body = self._body()
            if body is not None:
                self._proxy(method, "/voice/sdp", body, timeout=30)
            return
        if path == "/api/voice/hangup" and method == "POST":
            body = self._body()
            if body is not None:
                self._proxy(method, "/voice/hangup", body)
            return
        parts = [urllib.parse.unquote(part) for part in path.split("/") if part]
        if len(parts) >= 2 and parts[:2] == ["api", "external-sessions"]:
            self._external_sessions_route(parts, method, parsed.query)
            return
        if len(parts) == 3 and parts[:2] == ["api", "devices"] and method == "DELETE":
            device_id = parts[2]
            if not _SESSION_ID.fullmatch(device_id):
                self._json(400, {"error": "Некорректный идентификатор устройства"})
                return
            purge = (urllib.parse.parse_qs(parsed.query).get("purge") or ["0"])[0].lower() in {"1", "true", "yes"}
            suffix = "?purge=1" if purge else ""
            self._proxy(method, f"/devices/{urllib.parse.quote(device_id, safe='')}{suffix}")
            return
        if len(parts) == 4 and parts[:3] == ["api", "extensions", "skills"] and method == "PATCH":
            skill_id = parts[3]
            if not _SESSION_ID.fullmatch(skill_id):
                self._json(400, {"error": "Некорректный идентификатор скилла"})
                return
            body = self._body()
            if body is None:
                return
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                self._json(400, {"error": "Поле enabled должно быть логическим"})
                return
            self._proxy(method, f"/extensions/skills/{urllib.parse.quote(skill_id, safe='')}", {"enabled": enabled})
            return
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "tasks"
            and parts[3] == "approvals"
            and method == "POST"
        ):
            self._resolve_approval(parts[2], parts[4])
            return
        if len(parts) == 5 and parts[:2] == ["api", "tasks"] and parts[3] == "user-input" and method == "POST":
            if not _TASK_ID.fullmatch(parts[2]) or not _TASK_ID.fullmatch(parts[4]):
                self._json(400, {"error": "Некорректный идентификатор запроса"})
                return
            body = self._body()
            if body is not None:
                self._proxy(method, f"/tasks/{urllib.parse.quote(parts[2], safe='')}/user-input/{urllib.parse.quote(parts[4], safe='')}", body)
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "approvals":
            self._json(405, {"error": "Решение по запросу отправляется методом POST с идентификатором запроса"})
            return
        if len(parts) >= 3 and parts[:2] == ["api", "sessions"]:
            session_id = parts[2]
            if not _SESSION_ID.fullmatch(session_id):
                self._json(400, {"error": "Некорректный идентификатор сессии"})
                return
            quoted = urllib.parse.quote(session_id, safe="")
            if len(parts) == 4 and parts[3] == 'profile' and method == 'POST':
                body = self._body()
                if body is None:
                    return
                if set(body) != {'profile'} or not isinstance(body['profile'], str) or not _PROFILE_NAME.fullmatch(body['profile']):
                    self._json(400, {'error': 'Некорректный профиль'})
                    return
                self._proxy(method, f'/sessions/{quoted}/profile', body)
                return
            if len(parts) == 4 and parts[3] == "terminal" and method == "POST":
                self._terminal_ticket(session_id)
                return
            if len(parts) == 4 and parts[3] == "activity" and method == "GET":
                self._session_activity(session_id)
                return
            if len(parts) == 6 and parts[3] == "activity" and parts[5] == "stop" and method == "POST":
                body = self._body()
                if body is None:
                    return
                identity = parts[4]
                if not _SESSION_ID.fullmatch(identity):
                    self._json(400, {"error": "Некорректный процесс"})
                    return
                if identity.startswith("webterm-"):
                    token = self._token()
                    if not token:
                        self._json(401, {"error": "Требуется подключение"})
                        return
                    status, data, _ = self._upstream("GET", f"/sessions/{quoted}/activity", token)
                    if status >= 400:
                        self._json(status, self._decode(data, "Сессия недоступна"))
                        return
                    from ..core.mobile_bridge.activity import stop_terminal
                    if not stop_terminal(self.server.terminals, session_id, identity, "webterm-"):
                        self._json(404, {"error": "Терминал уже завершён или недоступен"})
                        return
                    self._json(200, {"id": identity, "requested": True})
                else:
                    self._proxy(method, f"/sessions/{quoted}/activity/{urllib.parse.quote(identity, safe='')}/stop", body)
                return
            if len(parts) == 4 and parts[3] == "cursor" and method == "GET":
                self._cursor(session_id)
                return
            if len(parts) == 4 and parts[3] == "cancel" and method == "POST":
                body = self._body()
                if body is None:
                    return
                mode = str(body.get("mode") or "cancel").strip().lower()
                if mode not in _CANCEL_MODES:
                    self._json(
                        400,
                        {"error": "Режим отмены должен быть cancel, stop или kill"},
                    )
                    return
                self._proxy(method, f"/sessions/{quoted}/cancel", {"mode": mode})
                return
            if len(parts) == 4 and parts[3] == "agents" and method == "GET":
                self._proxy(method, f"/sessions/{quoted}/agents")
                return
            if len(parts) == 4 and parts[3] == "subagents" and method == "GET":
                limit = self._query_int(parsed.query, "limit", 200, 1, 500)
                self._proxy(method, f"/sessions/{quoted}/subagents?limit={limit}")
                return
            if (
                len(parts) == 6
                and parts[3] == "subagents"
                and parts[5] == "cancel"
                and method == "POST"
            ):
                subagent_id = parts[4]
                if not _TASK_ID.fullmatch(subagent_id):
                    self._json(400, {"error": "Некорректный идентификатор субагента"})
                    return
                self._proxy(
                    method,
                    f"/sessions/{quoted}/subagents/"
                    f"{urllib.parse.quote(subagent_id, safe='')}/cancel",
                )
                return
            if len(parts) == 4 and parts[3] == "events" and method == "GET":
                # The same path returns two different things: a live SSE stream
                # (without limit) and a durable session log for history—agent plan and usage
                # (with limit). The bridge maintains them on separate
                # endpoints, and the browser must not confuse cursors.
                if "limit" in urllib.parse.parse_qs(parsed.query):
                    self._session_journal(session_id, parsed.query)
                else:
                    self._events(session_id, parsed.query)
                return
            if len(parts) == 5 and parts[3:5] == ["changes", "review"] and method == "GET":
                query = urllib.parse.parse_qs(parsed.query)
                allowed = {key: (query.get(key) or [default])[0] for key, default in (("mode", "session"), ("base", ""), ("path", ""), ("revision", ""), ("include_untracked", "0"))}
                self._proxy(method, f"/sessions/{quoted}/changes/review?" + urllib.parse.urlencode(allowed))
                return
            if len(parts) == 4 and parts[3] == "changes" and method == "GET":
                self._proxy(method, f"/sessions/{quoted}/changes")
                return
            if len(parts) == 5 and parts[3] == "changes" and parts[4] == "diff" and method == "GET":
                self._diff_proxy(quoted, "changes/diff", parsed.query)
                return
            if len(parts) == 4 and parts[3] == "checkpoints" and method == "GET":
                limit = self._query_int(parsed.query, "limit", 50, 1, 200)
                offset = self._query_int(parsed.query, "offset", 0, 0, 1_000_000)
                self._proxy(method, f"/sessions/{quoted}/checkpoints?limit={limit}&offset={offset}")
                return
            if (
                len(parts) == 6
                and parts[3] == "checkpoints"
                and parts[5] == "changes"
                and method == "GET"
            ):
                revision = parts[4]
                if not _SESSION_ID.fullmatch(revision):
                    self._json(400, {"error": "Некорректная версия чекпоинта"})
                    return
                self._proxy(
                    method,
                    f"/sessions/{quoted}/checkpoints/{urllib.parse.quote(revision, safe='')}/changes",
                )
                return
            if (
                len(parts) == 6
                and parts[3] == "checkpoints"
                and parts[5] == "diff"
                and method == "GET"
            ):
                revision = parts[4]
                if not _SESSION_ID.fullmatch(revision):
                    self._json(400, {"error": "Некорректная версия чекпоинта"})
                    return
                self._diff_proxy(
                    quoted,
                    f"checkpoints/{urllib.parse.quote(revision, safe='')}/diff",
                    parsed.query,
                )
                return
            if len(parts) == 4 and parts[3] == "files" and method == "GET":
                self._proxy(method, f"/sessions/{quoted}/files")
                return
            if len(parts) == 4 and parts[3] == "sources" and method == "GET":
                self._proxy(method, f"/sessions/{quoted}/sources")
                return
            if len(parts) == 5 and parts[3] == "uploads" and method == "GET":
                if not re.fullmatch(r"[0-9a-f]{32}", parts[4]):
                    self._json(400, {"error": "Некорректное вложение"})
                    return
                self._download(session_id, parts[4], preview=True, resource="uploads")
                return
            if len(parts) == 5 and parts[3] == "files" and method == "GET":
                # Download is an identifier (sha256) that the bridge
                # compares with the actual file state on disk.
                file_name = parts[4]
                if not _SESSION_ID.fullmatch(file_name):
                    self._json(400, {"error": "Некорректное имя файла"})
                    return
                query = urllib.parse.parse_qs(parsed.query)
                preview = str((query.get("preview") or ["0"])[0]).lower() in {"1", "true", "yes"}
                self._download(session_id, file_name, preview=preview)
                return
            if len(parts) == 4 and parts[3] == "tree" and method == "GET":
                self._session_tree(session_id, parsed.query)
                return
            if len(parts) == 4 and parts[3] == "reveal" and method == "POST":
                self._reveal(session_id)
                return
            if len(parts) == 3 and method == "PATCH":
                body = self._body()
                if body is None:
                    return
                title = body.get("title")
                if title is not None and not isinstance(title, str):
                    self._json(400, {"error": "Поле title должно быть строкой"})
                    return
                if 'pinned' in body and not isinstance(body['pinned'],bool):
                    self._json(400,{'error':'Поле pinned должно быть boolean'});return
                self._proxy(method, f"/sessions/{quoted}", body)
                return
            if len(parts) == 3 and method == "GET":
                self._proxy(method, f"/sessions/{quoted}")
                return
            if len(parts) == 4 and parts[3] == "messages" and method == "POST":
                body = self._body()
                if body is not None:
                    self._proxy(method, f"/sessions/{quoted}/messages", body)
                return
            if (
                len(parts) == 6
                and parts[3] == "messages"
                and parts[5] == "send-now"
                and method == "POST"
            ):
                body = self._body()
                if body is None:
                    return
                if not _TASK_ID.fullmatch(parts[4]):
                    self._json(400, {"error": "Некорректный идентификатор сообщения"})
                    return
                self._proxy(
                    method,
                    f"/sessions/{quoted}/messages/"
                    f"{urllib.parse.quote(parts[4], safe='')}/send-now",
                    body,
                )
                return
            if (
                len(parts) == 6
                and parts[3] == "messages"
                and parts[5] == "cancel"
                and method == "POST"
            ):
                body = self._body()
                if body is None:
                    return
                if not _TASK_ID.fullmatch(parts[4]):
                    self._json(400, {"error": "Некорректный идентификатор сообщения"})
                    return
                self._proxy(
                    method,
                    f"/sessions/{quoted}/messages/"
                    f"{urllib.parse.quote(parts[4], safe='')}/cancel",
                    body,
                )
                return
            if len(parts) == 4 and parts[3] == "uploads" and method == "POST":
                self._upload(session_id)
                return
        self._json(404, {"error": "Страница не найдена"})

    def _check_origin(self, method: str) -> bool:
        host = f"127.0.0.1:{self.server.server_port}"
        if str(self.headers.get("Host") or "").lower() != host:
            self._host_hint()
            return False
        if method != "GET":
            site = str(self.headers.get("Sec-Fetch-Site") or "")
            if str(self.headers.get("Origin") or "") != self.origin or site not in {"", "same-origin", "none"}:
                self._json(403, {"error": "Запрос отклонён: источник не совпадает с локальным интерфейсом"})
                return False
        return True

    def _host_hint(self) -> None:
        """Browser hint: if accessed via hostname instead of 127.0.0.1."""
        if "text/html" not in str(self.headers.get("Accept") or "").lower():
            self._json(421, {"error": "Используйте адрес 127.0.0.1, показанный в терминале"})
            return
        self._html(
            421,
            "Адрес не совпадает",
            "Интерфейс слушает только <code>"
            f"{escape(self.origin)}</code>. Откройте именно эту ссылку — её печатает в терминале "
            "запуск <code>igorek-factory web</code>.",
        )

    def _html(self, status: int, title: str, message: str) -> None:
        body = (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(title)}</title><style>"
            "body{background:#111315;color:#e8e6e1;font:16px/1.6 system-ui,-apple-system,sans-serif;"
            "display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}"
            "main{max-width:34rem;padding:2rem 1.5rem}h1{font-size:1.35rem;margin:0 0 .75rem}"
            "p{color:#a9a49c;margin:0}code{background:#1c1f22;color:#e8e6e1;padding:.15rem .35rem;border-radius:.3rem}"
            "</style></head><body><main>"
            f"<h1>{escape(title)}</h1><p>{message}</p></main></body></html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        self.wfile.write(body)

    def _headers(self) -> None:
        nonce = getattr(self, "_style_nonce", "")
        style_sources = "'self'" + (f" 'nonce-{nonce}'" if nonce else "")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; worker-src 'self'; "
            f"style-src {style_sources}; img-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        # The microphone is only permitted for the interface itself: the voice input button
        # uses the Web Speech API, and without `microphone=(self)`, the browser rejects
        # recognition with a `not-allowed` response. Camera and geolocation remain disabled.
        self.send_header("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
        self.send_header("Cache-Control", "no-store")

    def _preview_headers(self) -> None:
        """Preview document headers: isolated CSP and iframe restricted to its own origin."""
        self.send_header("Content-Security-Policy", _PREVIEW_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store")

    def _json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        cookie: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self._headers()
        if cookie is not None:
            if cookie:
                value = f"{_COOKIE}={cookie}; Path=/; Max-Age=7776000; Secure; HttpOnly; SameSite=Strict"
            else:
                value = f"{_COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"
            self.send_header("Set-Cookie", value)
        self.end_headers()
        self.wfile.write(data)

    def _static(self, name: str) -> None:
        if name == "alarm1.mp3":
            name = "audio/task-completed.mp3"
        try:
            data = (_STATIC / name).read_bytes()
        except OSError:
            self._json(500, {"error": "Файлы веб-интерфейса не установлены"})
            return
        content_type = _STATIC_TYPES[name]
        if name == "index.html":
            self._style_nonce = secrets.token_urlsafe(24)
            data = data.replace(b"<head>", ('<head><meta name="igorek-style-nonce" content="' + self._style_nonce + '">').encode(), 1)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._headers()
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any] | None:
        if "application/json" not in str(self.headers.get("Content-Type") or "").lower():
            self._json(415, {"error": "Ожидается JSON-запрос"})
            return None
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size <= 0 or size > _MAX_BODY:
                raise ValueError
            value = json.loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "Некорректный JSON или размер запроса"})
            return None
        if not isinstance(value, dict):
            self._json(400, {"error": "JSON-запрос должен содержать объект"})
            return None
        return value

    def _token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(str(self.headers.get("Cookie") or ""))
        except Exception:
            return ""
        morsel = cookie.get(_COOKIE)
        token = morsel.value if morsel else ""
        return token if len(token) <= 512 else ""

    def _local_token(self) -> str:
        """Validate a device capability before reading or changing local state."""
        token = self._token()
        if not token:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return ""
        status, data, _ = self._upstream("GET", "/devices", token)
        if status in {401, 403}:
            self._json(401, {"error": "Доступ браузера отозван. Подключите его заново."}, cookie="")
            return ""
        try:
            payload = json.loads(data)
            valid = status == 200 and isinstance(payload, dict) and bool(payload.get("current_device_id"))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            self._json(503, {"error": "Не удалось проверить доступ браузера"})
            return ""
        return token

    def _upstream(
        self,
        method: str,
        path: str,
        token: str = "",
        body: dict[str, Any] | None = None,
        timeout: float = 20,
        *,
        raw: bytes | None = None,
        content_type: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, str]:
        headers = {"Accept": "application/json"}
        operation_id = str(self.headers.get('X-Client-Operation-Id') or '')
        if re.fullmatch(r'[A-Za-z0-9_-]{8,160}', operation_id):
            headers['X-Client-Operation-Id'] = operation_id
        data: bytes | None = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif raw is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
            headers["Content-Length"] = str(len(raw))
            data = raw
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            f"{self.bridge_url}/mobile/v1{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            data = json.dumps(
                {"error": f"Не удалось связаться с mobile bridge: {reason}"}, ensure_ascii=False
            ).encode("utf-8")
            return 502, data, "application/json"

    def _relay(self, status: int, data: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime == "application/json" else mime)
        self.send_header("Content-Length", str(len(data)))
        self._headers()
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 20) -> None:
        token = self._token()
        if not token:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        status, data, mime = self._upstream(method, path, token, body, timeout)
        if status == 401:
            self._json(401, {"error": "Доступ браузера отозван. Подключите его заново."}, cookie="")
            return
        self._relay(status, data, mime)

    def _upload(self, session_id: str) -> None:
        """Accepted attachment: request body — raw bytes, metadata — in headers."""
        token = self._token()
        if not token:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > _MAX_UPLOAD:
            self.close_connection = True
            self._json(
                413,
                {"error": f"Вложение должно быть не пустым и не больше {_MAX_UPLOAD // (1024 * 1024)} МБ"},
            )
            return
        data = bytearray()
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    raise ValueError("upload body is incomplete")
                data.extend(chunk)
                remaining -= len(chunk)
        except (OSError, ValueError):
            self.close_connection = True
            self._json(400, {"error": "Не удалось прочитать вложение"})
            return
        mime = str(self.headers.get("Content-Type") or "").split(";")[0].strip()[:200] or "application/octet-stream"
        filename = re.sub(r"[\x00-\x1f\x7f]+", "", str(self.headers.get("X-File-Name") or ""))[:_MAX_UPLOAD_HEADER]
        extra = {"X-File-Name": filename} if filename else None
        status, payload, resp_mime = self._upstream(
            "POST",
            f"/sessions/{urllib.parse.quote(session_id, safe='')}/uploads",
            token,
            timeout=60,
            raw=bytes(data),
            content_type=mime,
            extra_headers=extra,
        )
        if status == 401:
            self._json(401, {"error": "Доступ браузера отозван. Подключите его заново."}, cookie="")
            return
        self._relay(status, payload, resp_mime)

    def _proxy_json(self, method: str, path: str) -> None:
        """Proxying GET/PATCH pair: PATCH body is parsed as JSON."""
        if method == "GET":
            self._proxy(method, path)
            return
        body = self._body()
        if body is not None:
            self._proxy(method, path, body)

    @staticmethod
    def _query_int(raw_query: str, name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int((urllib.parse.parse_qs(raw_query).get(name) or [default])[0])
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    def _terminal_ticket(self, session_id: str) -> None:
        """Issue a one-time ticket for this session’s terminal.

        The ticket is the sole way to open a shell: it is session-bound,
        expires after TTL seconds, and is invalidated on first use. A
        linked cookie is required: otherwise, the route returns 401 before the
        manager even processes the request.
        """
        if not self._token():
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        manager = getattr(self.server, "terminals", None)
        if manager is None:  # pragma: no cover - The server always creates a manager
            self._json(503, {"error": "Терминал недоступен на этом сервере"})
            return
        try:
            self._terminal_workdir(session_id)
            ticket = manager.issue_ticket(session_id, device_identity=hashlib.sha256(self._token().encode()).hexdigest())
        except TerminalError as exc:
            self._json(400, {"error": f"Не удалось выдать билет терминала: {exc.reason}"})
            return
        self._json(200, {"ticket": str(ticket), "ttl_s": int(TERMINAL_TICKET_TTL_S),
                         "execution_mode": self.server._terminal_modes[session_id]})

    def _terminal_workdir(self, session_id: str) -> str:
        token = self._token()
        if not token:
            raise TerminalError("device_unauthorized")
        status, data, _mime = self._upstream(
            "GET", f"/sessions/{urllib.parse.quote(session_id, safe='')}", token
        )
        if status != 200:
            raise TerminalError("session_access_denied")
        try:
            session = json.loads(data)
        except (ValueError, TypeError):
            raise TerminalError("session_mode_unknown") from None
        if session.get("isolation") == "kvm-unavailable":
            raise TerminalError("kvm_terminal_unavailable")
        if session.get("isolation") not in {"none", "kvm"}:
            raise TerminalError("session_mode_unknown")
        if session["isolation"] == "none" and os.environ.get("FACTORY_WEB_HOST_TERMINAL") != "1":
            raise TerminalError("host_terminal_disabled")
        cwd = (session.get("meta") or {}).get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.isdir(cwd):
            raise TerminalError("workdir_missing")
        self.server._terminal_modes[session_id] = session["isolation"]
        return cwd

    def _terminal_ws(self) -> None:
        """Upgrade GET /api/terminal to a terminal WebSocket.

        Pre-upgrade checks: Host (general), Origin is mandatory and must match
        the interface’s origin—browsers send it during WS handshake, while a
        local process without a correct Origin receives a 403. Shell access
        is granted only via a one-time ticket issued via a linked cookie.
        """
        manager = getattr(self.server, "terminals", None)
        if manager is None:  # pragma: no cover
            self._json(503, {"error": "Терминал недоступен на этом сервере"})
            return
        origin = str(self.headers.get("Origin") or "")
        if origin != self.origin:
            self._json(403, {"error": "Запрос отклонён: источник не совпадает с локальным интерфейсом"})
            return
        site = str(self.headers.get("Sec-Fetch-Site") or "")
        if site not in {"", "same-origin", "none"}:
            self._json(403, {"error": "Запрос отклонён: источник не совпадает с локальным интерфейсом"})
            return
        request = HandshakeRequest.from_headers(self.headers)
        try:
            accept = validate_handshake(request)
        except WebSocketError as exc:
            self._json(400, {"error": f"Некорректный WebSocket-handshake: {exc.reason}"})
            return
        self.send_response(101, "Switching Protocols")
        for name, value in handshake_headers(accept):
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:  # pragma: no cover - The client has already disconnected
            return
        # After a 101 connection, it belongs to a terminal loop: the standard HTTP layer
        # no longer reads from it, and its closure is handled by the endpoint itself.
        socket_obj = self.connection
        try:
            socket_obj.settimeout(None)
        except OSError:  # pragma: no cover
            pass
        ws = WebSocket(socket_obj, max_message_bytes=WS_MAX_MESSAGE_BYTES)
        log = getattr(self.server, "terminal_log", None) or TerminalLog()
        TerminalEndpoint(
            ws, manager, workdir_provider=self._terminal_workdir,
            access_validator=self._terminal_workdir, log=log,
            device_identity=hashlib.sha256(self._token().encode()).hexdigest(),
        ).serve()
        self.close_connection = True

    def _diff_proxy(self, session_id: str, suffix: str, raw_query: str) -> None:
        """Single file diff: the path value is passed to the bridge as a query parameter."""
        path = str((urllib.parse.parse_qs(raw_query).get("path") or [""])[0]).strip()
        if not path:
            self._json(400, {"error": "Не указан путь файла для диффа"})
            return
        target = f"/sessions/{session_id}/{suffix}?" + urllib.parse.urlencode({"path": path})
        self._proxy("GET", target)

    def _session_activity(self, session_id: str) -> None:
        token = self._token()
        if not token:
            self._json(401, {"error": "Требуется подключение"})
            return
        status, data, _ = self._upstream("GET", f"/sessions/{urllib.parse.quote(session_id, safe='')}/activity", token)
        payload = self._decode(data, "Не удалось получить процессы")
        if status < 400:
            from ..core.mobile_bridge.activity import terminal_process
            payload["processes"] = list(payload.get("processes") or []) + terminal_process(self.server.terminals, session_id, "webterm-")
        self._json(status, payload)

    def _download(self, session_id: str, file_name: str, *, preview: bool = False, resource: str = "files") -> None:
        """Session file delivery: the bridge's status, type, and bytes are sent without JSON parsing.

        preview=True — preview artifact mode: Content-Disposition is set to inline, and the response receives an isolated CSP instead of the general CSP interface (otherwise, HTML/SVG documents remain without styles and scripts).
        """
        token = self._token()
        if not token:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        target = (
            f"/sessions/{urllib.parse.quote(session_id, safe='')}"
            f"/{resource}/{urllib.parse.quote(file_name, safe='')}"
        )
        request_headers = {"Authorization": f"Bearer {token}", "Accept": "*/*"}
        if self.headers.get("Range"):
            request_headers["Range"] = self.headers["Range"]
        request = urllib.request.Request(
            f"{self.bridge_url}/mobile/v1{target}", headers=request_headers,
        )
        try:
            upstream = urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                self.send_response(416)
                self.send_header("Content-Range", exc.headers.get("Content-Range") or "bytes */0")
                self.send_header("Content-Length", "0")
                self._headers()
                self.end_headers()
                return
            if exc.code == 401:
                self._json(401, {"error": "Доступ браузера отозван. Подключите его заново."}, cookie="")
                return
            self._json(exc.code, self._decode(exc.read(), "Не удалось скачать файл"))
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            self._json(502, {"error": f"Не удалось скачать файл: {reason}"})
            return
        with upstream:
            self.connection.settimeout(120)
            content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
            prefix = b""
            prepared = None
            if preview and upstream.status == 200 and content_type.split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}:
                prefix = upstream.read(MAX_PREVIEW_HTML_BYTES + 1)
                if len(prefix) <= MAX_PREVIEW_HTML_BYTES:
                    prepared = prepare_html_preview(prefix)
            self.send_response(upstream.status)
            self.send_header("Content-Type", _preview_content_type(content_type) if preview else content_type)
            if preview:
                self.send_header("Content-Disposition", "inline")
            else:
                disposition = upstream.headers.get("Content-Disposition")
                if disposition:
                    self.send_header("Content-Disposition", disposition)
            for name in ("Content-Range", "Accept-Ranges"):
                if upstream.headers.get(name):
                    self.send_header(name, upstream.headers[name])
            length = str(len(prepared)) if prepared is not None else upstream.headers.get("Content-Length")
            if length:
                self.send_header("Content-Length", length)
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            if preview:
                self._preview_headers()
            else:
                self._headers()
            self.end_headers()
            try:
                if prepared is not None:
                    self.wfile.write(prepared)
                    return
                if prefix:
                    self.wfile.write(prefix)
                while True:
                    chunk = upstream.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass

    def _session_tree(self, session_id: str, raw_query: str) -> None:
        """Session working directory files for '@' mentions in the composer."""
        if not self._local_token():
            return
        query = urllib.parse.parse_qs(raw_query)
        relative = str((query.get("path") or [""])[0]).strip()
        if len(relative) > _MENTION_MAX_PATH:
            self._json(400, {"error": "Слишком длинный путь"})
            return
        depth = self._query_int(raw_query, "depth", 1, 1, _MENTION_MAX_DEPTH)
        try:
            root = _mention_tree_root(session_id)
            base = _mention_subdir(root, relative)
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
            return
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
            return
        except (ValueError, NotADirectoryError) as exc:
            self._json(400, {"error": str(exc)})
            return
        entries, truncated = _mention_entries(root, base, depth)
        self._json(200, {"entries": entries, "truncated": truncated, "depth": depth})

    def _reveal(self, session_id: str) -> None:
        """Open session file in the desktop file manager.

        The path resolves strictly within the session working directory via the same check as the mention tree. A filename without a path (bridge subfolders do not return a path) is searched in the working directory and accepted only upon a one-to-one match.
        """
        if not self._local_token():
            return
        body = self._body()
        if body is None:
            return
        raw = body.get("path", "")
        if not isinstance(raw, str):
            self._json(400, {"error": "Поле path должно быть строкой", "code": "bad_path"})
            return
        relative = raw.strip()
        if not relative:
            self._json(400, {"error": "Не указан путь файла", "code": "bad_path"})
            return
        if len(relative) > _MENTION_MAX_PATH:
            self._json(400, {"error": "Слишком длинный путь", "code": "bad_path"})
            return
        try:
            root = _mention_tree_root(session_id)
            target = _mention_target(root, relative)
        except PermissionError as exc:
            self._json(403, {"error": str(exc), "code": "forbidden"})
            return
        except FileNotFoundError as exc:
            if "/" in relative or "\\" in relative:
                self._json(404, {"error": str(exc), "code": "not_found"})
                return
            try:
                target = _mention_find_by_name(root, relative)
            except PermissionError as inner:
                self._json(403, {"error": str(inner), "code": "forbidden"})
                return
            except FileNotFoundError as inner:
                self._json(404, {"error": str(inner), "code": "not_found"})
                return
            except ValueError as inner:
                self._json(409, {"error": str(inner), "code": "ambiguous"})
                return
        except (ValueError, NotADirectoryError) as exc:
            self._json(400, {"error": str(exc), "code": "bad_path"})
            return
        opener, error_code, error_text = _reveal_open(target)
        if error_code:
            status = 503 if error_code == "no_display" else 502
            self._json(status, {"error": error_text, "code": error_code})
            return
        shown = "" if target == root else target.relative_to(root).as_posix()
        self._json(200, {"ok": True, "path": shown, "opener": opener})

    def _model_info(self, raw_query: str) -> None:
        """Actual profile model for the Igorek composer chip and context window.

        Local profiles resolve the running server's model. Cloud profiles keep
        their configured model; the passed model is a fallback for missing profiles.
        """
        if not self._local_token():
            return
        query = urllib.parse.parse_qs(raw_query)
        profile = str((query.get("profile") or [""])[0]).strip()
        passed = str((query.get("model") or [""])[0]).strip()
        if profile and not _PROFILE_NAME.fullmatch(profile):
            self._json(400, {"error": "Некорректное имя профиля"})
            return
        model = _profile_model(profile) or passed
        self._json(
            200,
            {"profile": profile, "model": model, "context_window": _profile_context_window(profile, model)},
        )

    def _provider_usage(self, raw_query: str) -> None:
        if not self._local_token():
            return
        query = urllib.parse.parse_qs(raw_query)
        profile = str((query.get('profile') or [''])[0])
        if not _PROFILE_NAME.fullmatch(profile):
            self._json(400, {"error": "Некорректное имя профиля", "code": "invalid_profile"})
            return
        provider = getattr(self.server, 'profile_usage_provider', None)
        if provider is None:
            self._json(200, {"provider": "unsupported", "profile": profile, "usage": None})
            return
        try:
            self._json(200, provider(profile, force=(query.get('refresh') or ['0'])[0] == '1'))
        except _OpenCodeUsageError as error:
            self._json(error.status, {"error": error.message, "code": error.code})
        except Exception:
            self._json(503, {"error": "Квота сейчас недоступна", "code": "unavailable"})

    def _opencode_usage(self, raw_query: str) -> None:
        if not self._local_token():
            return
        query = urllib.parse.parse_qs(raw_query)
        force = str((query.get("refresh") or ["0"])[0]).strip().lower() in {"1", "true", "yes"}
        provider = getattr(self.server, "opencode_usage_provider", None)
        if provider is not None:
            try:
                self._json(200, provider(force=force))
            except _OpenCodeUsageError as exc:
                self._json(exc.status, {"error": exc.message, "code": exc.code})
            except Exception:
                self._json(502, {"error": "Не удалось получить квоту OpenCode", "code": "network"})
            return
        now = time.monotonic()
        with _OPENCODE_CACHE_LOCK:
            cached = _OPENCODE_USAGE_CACHE.get("payload")
            fresh = cached is not None and now - float(_OPENCODE_USAGE_CACHE.get("at") or 0.0) < _OPENCODE_USAGE_CACHE_S
        if cached is not None and fresh and not force:
            self._json(200, cached)
            return
        key = _opencode_api_key()
        if not key:
            self._json(503, {"error": "Ключ OpenCode не найден на этом компьютере", "code": "no_key"})
            return
        try:
            payload = _fetch_opencode_usage(key)
        except _OpenCodeUsageError as exc:
            self._json(exc.status, {"error": exc.message, "code": exc.code})
            return
        except Exception:
            self._json(502, {"error": "Не удалось получить квоту OpenCode", "code": "network"})
            return
        result = {"usage": payload["usage"], "fetched_at": datetime.now(UTC).isoformat()}
        with _OPENCODE_CACHE_LOCK:
            _OPENCODE_USAGE_CACHE.update({"at": time.monotonic(), "payload": result})
        self._json(200, result)

    def _about(self) -> None:
        """Installed package version and mobile bridge address for the control panel."""
        try:
            version = package_version("igorek-agent-factory")
        except PackageNotFoundError:
            version = "не установлен"
        except Exception:  # pragma: no cover - protection against unexpected metadata failures
            version = "неизвестно"
        parsed = urllib.parse.urlsplit(self.bridge_url)
        self._json(
            200,
            {
                "version": version,
                "bridge_scheme": parsed.scheme,
                "bridge_host": parsed.hostname or "",
                "bridge_port": parsed.port,
            },
        )

    def _logs(self, raw_query: str) -> None:
        """Tail of the allowed unit log; keys and lines are validated here."""
        if not self._local_token():
            return
        query = urllib.parse.parse_qs(raw_query)
        key = str((query.get("unit") or [""])[0]).strip()
        provider = getattr(self.server, "logs_provider", None)
        if provider is not None:
            lines = self._query_int(raw_query, "lines", _LOG_LINES_DEFAULT, 1, _LOG_LINES_MAX)
            try:
                payload = provider(key, lines)
            except ValueError:
                self._json(400, {"error": "Неизвестный источник журнала desktop"})
                return
            self._json(200, payload)
            return
        unit = _log_unit_for_key(key)
        if not unit:
            self._json(
                400,
                {"error": "Неизвестный сервис. Доступны: gateway, mobile, web, worker:<профиль>"},
            )
            return
        lines = self._query_int(raw_query, "lines", _LOG_LINES_DEFAULT, 1, _LOG_LINES_MAX)
        rows, truncated, error = _journal_lines(unit, lines)
        if error:
            self._json(502, {"error": f"Не удалось прочитать журнал {unit}: {error}"})
            return
        self._json(200, {"unit": unit, "lines": rows, "truncated": truncated})

    def _logs_status(self) -> None:
        """State of allowed `systemd` units for badge interfaces."""
        if not self._local_token():
            return
        provider = getattr(self.server, "logs_status_provider", None)
        if provider is not None:
            self._json(200, provider())
            return
        units, error = _units_status()
        payload: dict[str, Any] = {"units": units}
        if error:
            payload["error"] = error
        self._json(200, payload)

    def _external_sessions_route(self, parts: list[str], method: str, raw_query: str) -> None:
        """Agent history routes: `/api/external-sessions[/{source}/{id}[/import]]`."""
        if len(parts) == 2 and method == "GET":
            self._external_sessions(raw_query)
            return
        if len(parts) == 4 and method == "GET":
            self._external_session_detail(parts[2], parts[3], raw_query)
            return
        if len(parts) == 5 and parts[4] == "import" and method == "POST":
            self._external_session_import(parts[2], parts[3])
            return
        self._json(404, {"error": "Страница не найдена"})

    @staticmethod
    def _external_source(source: str) -> str:
        value = str(source or "").strip().lower()
        return value if value in _EXTERNAL_SOURCES else ""

    def _external_sessions(self, raw_query: str) -> None:
        """External session catalog: bridge filters are passed through unchanged."""
        query = urllib.parse.parse_qs(raw_query)
        source = str((query.get("source") or [""])[0]).strip().lower()
        if source and source not in _EXTERNAL_SOURCES:
            self._json(400, {"error": "Неизвестный источник истории агентов"})
            return
        params: dict[str, str] = {"limit": str(self._query_int(raw_query, "limit", 1000, 1, 1000))}
        if source:
            params["source"] = source
        search = str((query.get("query") or [""])[0]).strip()
        if search:
            params["query"] = search[:200]
        if str((query.get("refresh") or ["0"])[0]).strip().lower() in {"1", "true", "yes"}:
            params["refresh"] = "1"
        self._proxy(
            "GET",
            "/external-sessions?" + urllib.parse.urlencode(params),
            timeout=_EXTERNAL_TIMEOUT_S,
        )

    def _external_session_detail(self, source: str, external_id: str, raw_query: str) -> None:
        """Transcript of a single external session with paginated output."""
        known = self._external_source(source)
        if not known or not _EXTERNAL_SESSION_ID.fullmatch(str(external_id or "")):
            self._json(400, {"error": "Некорректный источник или идентификатор внешней сессии"})
            return
        offset = self._query_int(raw_query, "offset", 0, 0, 1_000_000)
        limit = self._query_int(raw_query, "limit", 400, 1, 1000)
        target = (
            f"/external-sessions/{urllib.parse.quote(known, safe='')}"
            f"/{urllib.parse.quote(external_id, safe='')}?offset={offset}&limit={limit}"
        )
        self._proxy("GET", target, timeout=_EXTERNAL_TIMEOUT_S)

    def _external_session_import(self, source: str, external_id: str) -> None:
        """Import: a new chat for **Igorek** continues from an external transcript."""
        known = self._external_source(source)
        if not known or not _EXTERNAL_SESSION_ID.fullmatch(str(external_id or "")):
            self._json(400, {"error": "Некорректный источник или идентификатор внешней сессии"})
            return
        body = self._body()
        if body is None:
            return
        payload: dict[str, str] = {}
        for name in ("profile", "cwd", "idempotency_key"):
            value = body.get(name, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                self._json(400, {"error": f"Поле {name} должно быть строкой"})
                return
            payload[name] = value
        denied = _cwd_denied_reason(payload.get("cwd", ""))
        if denied:
            self._json(400, {"error": _CWD_DENIED_MESSAGES[denied], "code": "cwd_denied"})
            return
        target = (
            f"/external-sessions/{urllib.parse.quote(known, safe='')}"
            f"/{urllib.parse.quote(external_id, safe='')}/import"
        )
        self._proxy("POST", target, payload, timeout=_EXTERNAL_TIMEOUT_S)

    @staticmethod
    def _decode(data: bytes, fallback: str) -> dict[str, Any]:
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"error": fallback}
        return value if isinstance(value, dict) else {"error": fallback}

    def _status(self) -> None:
        token = self._token()
        if not token:
            self._json(200, {"paired": False})
            return
        status, data, _ = self._upstream("GET", "/devices", token)
        if status == 401:
            self._json(200, {"paired": False}, cookie="")
            return
        if status >= 400:
            self._json(status, self._decode(data, "Не удалось проверить подключение"))
            return
        value = self._decode(data, "Не удалось прочитать список устройств")
        device_id = str(value.get("current_device_id") or "")
        device = next(
            (item for item in value.get("devices", []) if str(item.get("device_id") or "") == device_id),
            {},
        )
        self._json(200, {"paired": True, "device": {"name": str(device.get("name") or "Локальный браузер")}})

    def _pair(self, body: dict[str, Any]) -> None:
        code = str(body.get("code") or "").strip()
        if len(code) < 6 or len(code) > 32:
            self.server.record_pair_failure()
            self._json(400, {"error": "Введите одноразовый код сопряжения из терминала"})
            return
        status, data, _ = self._upstream(
            "POST", "/pair/complete",
            body={"code": code, "name": "Igorek local web", "platform": "web"},
        )
        value = self._decode(data, "Не удалось подключить браузер")
        token = str(value.get("token") or "") if status < 400 else ""
        if not token or len(token) > 512:
            self.server.record_pair_failure()
            self._json(status if status >= 400 else 502, value)
            return
        self._json(
            201,
            {"paired": True, "device": {"name": value.get("name") or "Локальный браузер"}},
            cookie=token,
        )

    def _logout(self) -> None:
        token = self._token()
        if not token:
            self._json(200, {"logged_out": True}, cookie="")
            return
        status, data, _ = self._upstream("GET", "/devices", token)
        if status >= 400:
            self._json(status, self._decode(data, "Не удалось отозвать доступ браузера"))
            return
        device_id = str(self._decode(data, "Не удалось прочитать устройство").get("current_device_id") or "")
        if not device_id:
            self._json(502, {"error": "Bridge не вернул идентификатор браузера"})
            return
        status, data, _ = self._upstream(
            "DELETE", f"/devices/{urllib.parse.quote(device_id, safe='')}", token
        )
        if status >= 400:
            self._json(status, self._decode(data, "Не удалось отозвать доступ браузера"))
            return
        self._json(200, {"logged_out": True}, cookie="")

    def _cursor(self, session_id: str) -> None:
        """Session event catch-up point.

        The cursor is handled by the mobile bridge itself: the database path and its state remain shared among all clients, so the browser cannot diverge from the phone due to a different ``FACTORY_DATA_DIR``.
        """
        credential = self._token()
        if not credential:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        status, data, _ = self._upstream(
            "GET", f"/sessions/{urllib.parse.quote(session_id, safe='')}/cursor", credential
        )
        if status == 401:
            self._json(401, {"error": "Доступ браузера отозван. Подключите его заново."}, cookie="")
            return
        if status >= 400:
            self._json(status, self._decode(data, "Не удалось прочитать курсор событий"))
            return
        try:
            seq = max(0, int(self._decode(data, "Не удалось прочитать курсор событий").get("seq")))
        except (TypeError, ValueError):
            self._json(502, {"error": "Мост вернул некорректный курсор событий"})
            return
        self._json(200, {"seq": seq})

    def _approvals(self, query: dict[str, list[str]]) -> None:
        session_id = str((query.get("session_id") or [""])[0]).strip()
        if session_id and not _SESSION_ID.fullmatch(session_id):
            self._json(400, {"error": "Некорректный идентификатор сессии"})
            return
        suffix = f"?session_id={urllib.parse.quote(session_id, safe='')}" if session_id else ""
        self._proxy("GET", f"/approvals{suffix}")

    def _resolve_approval(self, task_id: str, request_id: str) -> None:
        if not _TASK_ID.fullmatch(task_id) or not _TASK_ID.fullmatch(request_id):
            self._json(400, {"error": "Некорректный идентификатор задачи или запроса"})
            return
        body = self._body()
        if body is None:
            return
        decision = str(body.get("decision") or "").strip().lower()
        if decision not in _APPROVAL_DECISIONS:
            self._json(400, {"error": "Решение должно быть allow_once, allow_session или reject"})
            return
        # The bridge waits for exactly a pair (task, request): it does not know which of the blocked actions to allow based on a single task_id.
        self._proxy(
            "POST",
            f"/tasks/{urllib.parse.quote(task_id, safe='')}/approvals/"
            f"{urllib.parse.quote(request_id, safe='')}",
            {"decision": decision},
        )

    def _session_journal(self, session_id: str, raw_query: str) -> None:
        """Durable session log page: agent schedule and usage history."""
        token = self._token()
        if not token:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        try:
            after = max(0, int((urllib.parse.parse_qs(raw_query).get("after") or ["0"])[0]))
        except (TypeError, ValueError):
            self._json(400, {"error": "Курсор событий должен быть числом"})
            return
        limit = self._query_int(raw_query, "limit", _EVENT_PAGE_DEFAULT, 1, _EVENT_PAGE_MAX)
        self._proxy(
            "GET",
            f"/sessions/{urllib.parse.quote(session_id, safe='')}/events?after={after}&limit={limit}",
        )

    def _events(self, session_id: str, raw_query: str) -> None:
        token = self._token()
        if not token:
            self._json(401, {"error": "Сначала подключите браузер одноразовым кодом"}, cookie="")
            return
        try:
            query = urllib.parse.parse_qs(raw_query)
            after = max(0, int((query.get("after") or ["0"])[0]))
        except ValueError:
            self._json(400, {"error": "Курсор событий должен быть числом"})
            return
        suffix = urllib.parse.urlencode({"after": after, "session_id": session_id})
        request = urllib.request.Request(
            f"{self.bridge_url}/mobile/v1/events?{suffix}",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        )
        try:
            upstream = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            self._json(exc.code, self._decode(exc.read(), "Не удалось открыть поток событий"))
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._json(502, {"error": f"Не удалось открыть поток событий: {getattr(exc, 'reason', None) or exc}"})
            return
        with upstream:
            self.connection.settimeout(65)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self._headers()
            self.end_headers()
            try:
                while True:
                    line = upstream.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    if line in {b"\n", b"\r\n"}:
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass
            self.close_connection = True


def run_server(args: Any) -> int:
    data_dir = str(getattr(args, "data_dir", "") or "").strip()
    if data_dir and not os.environ.get("FACTORY_DATA_DIR") and not os.environ.get("IGOREK_FACTORY_HOME"):
        os.environ["FACTORY_DATA_DIR"] = data_dir
    try:
        port = int(getattr(args, "port", 5177))
        if not 1 <= port <= 65535:
            raise ValueError("Порт веб-интерфейса должен быть в диапазоне 1–65535")
        bridge = _bridge_base(str(getattr(args, "bridge_url", "http://127.0.0.1:47821")))
        cert_arg = str(getattr(args, "tls_cert", "") or "").strip()
        key_arg = str(getattr(args, "tls_key", "") or "").strip()
        if bool(cert_arg) != bool(key_arg):
            raise ValueError("Для TLS укажите вместе --tls-cert и --tls-key")
        if cert_arg:
            cert_path, key_path, fingerprint = Path(cert_arg).expanduser(), Path(key_arg).expanduser(), "пользовательский"
        else:
            cert_path, key_path, fingerprint = _certificate_paths()
    except (OSError, ValueError) as exc:
        print(f"Ошибка запуска веб-интерфейса: {exc}")
        return 2
    try:
        server = _Server(port, bridge)
    except OSError as exc:
        print(f"Не удалось запустить локальный сервер на порту {port}: {exc}")
        return 2
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert_path), str(key_path))
        server.enable_tls(context)
    except (OSError, ssl.SSLError) as exc:
        server.server_close()
        print(f"Не удалось загрузить TLS-сертификат: {exc}")
        return 2
    print(f"Локальный веб-интерфейс: https://127.0.0.1:{port}")
    print("Сервер доступен только на этом компьютере. Для кода подключения: igorek-factory mobile pair")
    if not cert_arg:
        print(f"Создан локальный сертификат SHA-256 {fingerprint}; браузер может предупредить, что он не доверенный.")
        print(f"Публичный сертификат: {cert_path}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
