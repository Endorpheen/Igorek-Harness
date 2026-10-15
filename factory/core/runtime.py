from __future__ import annotations

import json
import os
import re
import shutil
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .codex_provider import resolve_codex_launch_settings

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI_HOME = Path.home() / ".igorek-agent-factory"
DEFAULT_CLAUDE_MODEL = "glm-4.7"
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
DEFAULT_CODEX_REASONING_EFFORT = "system"
DEFAULT_FILESYSTEM_MODE = "workspace"
DEFAULT_CHECKPOINTS_ENABLED = True
DEFAULT_CHECKPOINTS_MAX_SNAPSHOTS = 50
LEGACY_CLAUDE_BIN = Path.home() / ".nvm/versions/node/v22.21.1/bin/claude"


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _resolve_binary(env_name: str, default_name: str, legacy_path: Path | None = None) -> str:
    override = os.environ.get(env_name, "").strip()
    if override:
        if os.path.sep in override:
            return override
        resolved = shutil.which(override)
        if resolved:
            return resolved

    resolved = shutil.which(default_name)
    if resolved:
        return resolved

    if legacy_path is not None and legacy_path.exists():
        return str(legacy_path)

    return default_name


def is_installed_tool() -> bool:
    return "site-packages" in str(PACKAGE_ROOT)


def _runtime_home_has_state(root: Path) -> bool:
    markers = [
        root / "profiles",
        root / "database",
        root / "workspace",
        root / "MEMORY.md",
        root / ".bootstrap-state.json",
    ]
    return any(path.exists() for path in markers)


def get_runtime_layout() -> str:
    if _env_path("IGOREK_FACTORY_HOME") or _env_path("FACTORY_DATA_DIR"):
        return "env_override"
    forced_layout = os.environ.get("FACTORY_RUNTIME_LAYOUT", "").strip().lower()
    if forced_layout in {"home", "default-home"}:
        return "default_home"
    if forced_layout in {"repo", "repo-local", "package-root"}:
        return "repo_local"
    if is_installed_tool():
        return "default_home"
    if _runtime_home_has_state(DEFAULT_CLI_HOME):
        return "default_home"
    return "repo_local"


def get_data_root() -> Path:
    override = _env_path("IGOREK_FACTORY_HOME") or _env_path("FACTORY_DATA_DIR")
    if override:
        return override
    if get_runtime_layout() == "default_home":
        return DEFAULT_CLI_HOME
    return PACKAGE_ROOT


def get_db_path() -> str:
    return str(get_data_root() / "database" / "factory.db")


def get_workspace_root() -> str:
    return str(get_data_root() / "workspace")


def get_chat_sessions_dir() -> str:
    return str(get_data_root() / "chat_sessions")


def get_env_files_dir() -> Path:
    """Directory holding plaintext env files with secrets.

    Secrets must never live inside a repository working tree, so the default
    location is the data home. Override with ``FACTORY_ENV_FILES_DIR``.
    """
    override = _env_path("FACTORY_ENV_FILES_DIR")
    if override:
        return override
    return DEFAULT_CLI_HOME / "env"


def get_profiles_dir() -> str:
    override = _env_path("FACTORY_PROFILES_DIR")
    if override:
        return str(override)
    if _env_path("IGOREK_FACTORY_HOME") or _env_path("FACTORY_DATA_DIR"):
        return str(get_data_root() / "profiles")
    if get_runtime_layout() == "default_home":
        return str(get_data_root() / "profiles")
    return str(PACKAGE_ROOT / "config" / "profiles")


def get_bundled_profiles_dir() -> Path:
    """Profiles shipped with the package (used as fallback for get_profile_path)."""
    return PACKAGE_ROOT / "config" / "profiles"


def list_profile_names(profiles_dir: str | Path | None = None) -> list[str]:
    """Names of every available profile: runtime dir first, bundled dir as fallback."""
    bases: list[Path] = []
    if profiles_dir is not None:
        bases.append(Path(profiles_dir))
    else:
        bases.append(Path(get_profiles_dir()))
        bases.append(get_bundled_profiles_dir())
    names: list[str] = []
    for base in bases:
        try:
            if not base.is_dir():
                continue
        except OSError:
            continue
        for path in sorted(base.glob("*.json")):
            stem = path.stem
            if ".bak" in stem or stem.endswith(".new") or ".orig" in stem:
                continue
            if stem not in names:
                names.append(stem)
    return names


def get_profile_path(profile: str, profiles_dir: str | Path | None = None) -> Path:
    base = Path(profiles_dir) if profiles_dir is not None else Path(get_profiles_dir())
    candidate = base / f"{profile}.json"
    if candidate.exists() or profiles_dir is not None:
        return candidate
    fallback = PACKAGE_ROOT / "config" / "profiles" / f"{profile}.json"
    if fallback.exists():
        return fallback
    return candidate


def get_claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def resolve_claude_bin() -> str:
    return _resolve_binary("FACTORY_CLAUDE_BIN", "claude", LEGACY_CLAUDE_BIN)


def resolve_codex_bin() -> str:
    return _resolve_binary("FACTORY_CODEX_BIN", "codex")


def get_profile_config(profile: str, profiles_dir: str | Path | None = None) -> dict:
    profile_path = get_profile_path(profile, profiles_dir=profiles_dir)
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile config not found: {profile_path}")

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid profile config: {profile_path}")

    config = dict(data)
    config.setdefault("env", {})
    backend = str(config.get("backend", "claude")).strip() or "claude"
    config["backend"] = backend

    if backend == "claude":
        config.setdefault("provider", "z.ai")
        config.setdefault("model", config.get("env", {}).get("ANTHROPIC_MODEL") or DEFAULT_CLAUDE_MODEL)
    elif backend == "codex":
        config.setdefault("provider", "openai-codex")
        config.setdefault("model", DEFAULT_CODEX_MODEL)
        config["reasoning_effort"] = str(config.get("reasoning_effort", DEFAULT_CODEX_REASONING_EFFORT) or DEFAULT_CODEX_REASONING_EFFORT)
        if not isinstance(config.get("args"), list):
            config["args"] = []
    elif backend == "cc-backend":
        config.setdefault("base_url", "http://127.0.0.1:8001")
        config.setdefault("model", config.get("model", ""))
        config["filesystem_mode"] = profile_filesystem_mode(config)
        config["checkpoints"] = profile_checkpoint_settings(config)
    else:
        raise ValueError(f"Unsupported backend in profile {profile}: {backend}")

    return config


def profile_backend_name(profile_config: dict) -> str:
    return str(profile_config.get("backend", "claude")).strip() or "claude"


def profile_model_name(profile_config: dict) -> str:
    if profile_backend_name(profile_config) == "claude":
        return str(profile_config.get("model") or profile_config.get("env", {}).get("ANTHROPIC_MODEL") or DEFAULT_CLAUDE_MODEL)
    return str(profile_config.get("model") or DEFAULT_CODEX_MODEL)


_LOCAL_MODEL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

_GGUF_SHARD_SUFFIX_RE = re.compile(r"-\d{5}-of-\d{5}$")


def _clean_local_model_id(model_id: str) -> str:
    """Reduce a local server model id to a readable name for the TUI."""
    name = str(model_id or "").strip()
    if not name:
        return ""
    if "/" in name or "\\" in name:
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".gguf"):
        name = name[:-5]
    return _GGUF_SHARD_SUFFIX_RE.sub("", name).strip()


def resolve_local_model_display_name(base_url: str, fallback: str) -> str:
    """Return the model id currently served by a local OpenAI endpoint.

    Best-effort only: any error (server down, timeout, bad payload) yields
    ``fallback`` so the caller keeps the profile-configured name.
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return fallback
    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return fallback
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            cleaned = _clean_local_model_id(str(item.get("id") or ""))
            if cleaned:
                return cleaned
    return fallback


def profile_stable_prompt_prefix(profile_config: dict) -> bool:
    """Whether to keep a byte-stable system prompt for KV-cache reuse.

    Explicit ``stable_prompt_prefix`` wins when present. Otherwise the feature
    auto-enables for local endpoints: ``provider == "local"`` or a base_url
    pointing at a localhost host (llama-server and other local runtimes), so
    users do not have to manage the flag manually.
    """
    if "stable_prompt_prefix" in profile_config:
        return bool(profile_config.get("stable_prompt_prefix"))
    provider = str(profile_config.get("provider") or "").strip().lower()
    if provider == "local":
        return True
    base_url = str(profile_config.get("base_url") or "").strip()
    try:
        host = urlparse(base_url).hostname or ""
    except Exception:
        host = ""
    return host in _LOCAL_MODEL_HOSTS


def profile_display_model_name(profile_config: dict) -> str:
    """Like :func:`profile_model_name`, but local endpoints report the served model.

    The profile ``model`` is often a generic placeholder (e.g. ``local``) for a
    shared local profile; the actual model is what the server currently serves.
    """
    model = profile_model_name(profile_config)
    if profile_backend_name(profile_config) != "cc-backend":
        return model
    base_url = str(profile_config.get("base_url") or "").strip()
    try:
        host = urlparse(base_url).hostname or ""
    except Exception:
        host = ""
    if host in _LOCAL_MODEL_HOSTS:
        return resolve_local_model_display_name(base_url, model)
    return model


def profile_reasoning_effort(profile_config: dict) -> str:
    if profile_backend_name(profile_config) != "codex":
        return DEFAULT_CODEX_REASONING_EFFORT
    return str(profile_config.get("reasoning_effort") or DEFAULT_CODEX_REASONING_EFFORT)


def profile_codex_dangerous_mode(profile_config: dict) -> bool:
    if profile_backend_name(profile_config) != "codex":
        return False
    return bool(profile_config.get("dangerously_bypass_approvals_and_sandbox"))


def profile_filesystem_mode(profile_config: dict) -> str:
    normalized = str(profile_config.get("filesystem_mode") or DEFAULT_FILESYSTEM_MODE).strip().lower()
    if normalized not in {"workspace", "user-read", "full-dangerous"}:
        return DEFAULT_FILESYSTEM_MODE
    return normalized


def profile_checkpoint_settings(profile_config: dict) -> dict:
    raw = profile_config.get("checkpoints")
    nested = raw if isinstance(raw, dict) else {}
    enabled = nested.get("enabled")
    if enabled is None:
        enabled = profile_config.get("checkpoints_enabled", DEFAULT_CHECKPOINTS_ENABLED)
    max_snapshots = nested.get("max_snapshots")
    if max_snapshots is None:
        max_snapshots = profile_config.get("checkpoints_max_snapshots", DEFAULT_CHECKPOINTS_MAX_SNAPSHOTS)
    try:
        normalized_max = max(1, int(max_snapshots))
    except Exception:
        normalized_max = DEFAULT_CHECKPOINTS_MAX_SNAPSHOTS
    return {
        "enabled": bool(enabled),
        "max_snapshots": normalized_max,
    }


def build_backend_command(profile_config: dict, workdir: str, output_file: str | None = None, mcp_config_path: str | None = None) -> list[str]:
    backend = profile_backend_name(profile_config)

    if backend == "claude":
        command = [resolve_claude_bin()]
        if os.geteuid() != 0:
            command.append("--dangerously-skip-permissions")
        command.extend(["--print", "--verbose", "--output-format", "stream-json"])
        if mcp_config_path:
            command.extend(["--strict-mcp-config", "--mcp-config", mcp_config_path])
        return command

    if backend == "codex":
        codex_settings = resolve_codex_launch_settings(profile_config)
        command = [
            resolve_codex_bin(),
            "exec",
            "--skip-git-repo-check",
        ]
        if profile_codex_dangerous_mode(profile_config):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.append("--full-auto")
        command.extend(["-C", workdir])
        for override in codex_settings.get("config_overrides") or []:
            command.extend(["-c", str(override)])
        model = str(codex_settings.get("model") or profile_model_name(profile_config) or "").strip()
        if model:
            command.extend(["-m", model])
        reasoning_effort = str(codex_settings.get("reasoning_effort") or profile_reasoning_effort(profile_config) or "").strip()
        if reasoning_effort and reasoning_effort != DEFAULT_CODEX_REASONING_EFFORT:
            command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        codex_profile = str(codex_settings.get("codex_profile") or profile_config.get("codex_profile", "")).strip()
        if codex_profile:
            command.extend(["-p", codex_profile])
        extra_args = codex_settings.get("args")
        if not isinstance(extra_args, list):
            extra_args = profile_config.get("args", [])
        if isinstance(extra_args, list):
            command.extend(str(arg) for arg in extra_args)
        if output_file:
            command.extend(["-o", output_file])
        command.append("-")
        return command

    if backend == "cc-backend":
        return []

    raise ValueError(f"Unsupported backend: {backend}")
