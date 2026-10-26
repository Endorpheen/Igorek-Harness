from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .runtime import (
    get_data_root,
    get_profile_config,
    get_profile_path,
    profile_backend_name,
    resolve_codex_bin,
)

DEFAULT_MCP_REGISTRY: dict[str, Any] = {"mcpServers": {}}


def get_mcp_dir(data_dir: str | None = None) -> Path:
    return Path(data_dir).expanduser() / "mcp" if data_dir else get_data_root() / "mcp"


def get_mcp_registry_path(data_dir: str | None = None) -> Path:
    return get_mcp_dir(data_dir) / "servers.json"


def ensure_mcp_registry(data_dir: str | None = None) -> Path:
    path = get_mcp_registry_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_MCP_REGISTRY, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_mcp_registry(data_dir: str | None = None) -> dict[str, Any]:
    path = ensure_mcp_registry(data_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid MCP registry: {path}")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        data["mcpServers"] = {}
    return data


def save_mcp_registry(payload: dict[str, Any], data_dir: str | None = None) -> Path:
    path = ensure_mcp_registry(data_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def list_mcp_servers(data_dir: str | None = None) -> dict[str, Any]:
    return load_mcp_registry(data_dir).get("mcpServers", {})


def _apply_capability_metadata(
    entry: dict[str, Any],
    *,
    description: str | None = None,
    capabilities: list[str] | None = None,
    tools_hint: list[str] | None = None,
) -> None:
    if description:
        entry["description"] = str(description).strip()
    capability_items = [str(item).strip() for item in capabilities or [] if str(item).strip()]
    if capability_items:
        entry["capabilities"] = capability_items
    tool_items = [str(item).strip() for item in tools_hint or [] if str(item).strip()]
    if tool_items:
        entry["tools_hint"] = tool_items


def add_stdio_server(
    name: str,
    command: list[str],
    env: dict[str, str] | None = None,
    disabled: bool = False,
    data_dir: str | None = None,
    *,
    description: str | None = None,
    capabilities: list[str] | None = None,
    tools_hint: list[str] | None = None,
) -> dict[str, Any]:
    if not command:
        raise ValueError("command is required for stdio MCP server")
    registry = load_mcp_registry(data_dir)
    servers = registry.setdefault("mcpServers", {})
    entry: dict[str, Any] = {
        "type": "stdio",
        "command": command[0],
        "args": command[1:],
    }
    if env:
        entry["env"] = env
    if disabled:
        entry["disabled"] = True
    _apply_capability_metadata(
        entry,
        description=description,
        capabilities=capabilities,
        tools_hint=tools_hint,
    )
    servers[name] = entry
    save_mcp_registry(registry, data_dir)
    return entry


def add_http_server(
    name: str,
    url: str,
    headers: dict[str, str] | None = None,
    bearer_token_env_var: str | None = None,
    disabled: bool = False,
    data_dir: str | None = None,
    *,
    description: str | None = None,
    capabilities: list[str] | None = None,
    tools_hint: list[str] | None = None,
) -> dict[str, Any]:
    registry = load_mcp_registry(data_dir)
    servers = registry.setdefault("mcpServers", {})
    entry: dict[str, Any] = {
        "type": "http",
        "url": url,
    }
    if headers:
        entry["headers"] = headers
    if bearer_token_env_var:
        entry["bearer_token_env_var"] = bearer_token_env_var
    if disabled:
        entry["disabled"] = True
    _apply_capability_metadata(
        entry,
        description=description,
        capabilities=capabilities,
        tools_hint=tools_hint,
    )
    servers[name] = entry
    save_mcp_registry(registry, data_dir)
    return entry


def remove_mcp_server(name: str, data_dir: str | None = None) -> bool:
    registry = load_mcp_registry(data_dir)
    servers = registry.setdefault("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    save_mcp_registry(registry, data_dir)
    return True


def _load_profile(profile: str, data_dir: str | None = None) -> tuple[Path, dict[str, Any]]:
    profiles_dir = Path(data_dir).expanduser() / "profiles" if data_dir else None
    path = get_profile_path(profile, profiles_dir=profiles_dir)
    config = get_profile_config(profile, profiles_dir=profiles_dir)
    return path, config


def _save_profile(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_profile_mcp_servers(profile: str, data_dir: str | None = None) -> list[str]:
    _, config = _load_profile(profile, data_dir)
    access_path = get_mcp_dir(data_dir) / "access.json"
    if access_path.exists():
        try:
            if access_path.is_symlink() or access_path.stat().st_size > 1024*1024:
                return []
            policy = json.loads(access_path.read_text())
            if policy.get("version") != 1:
                return []
            return [name for name, profiles in policy["grants"].items()
                    if isinstance(profiles, dict) and any(isinstance(grant, dict) and grant.get("access") == "allow" and grant.get("config") == config for grant in profiles.values())]
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return []
    raw = config.get("mcp_servers") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def enable_mcp_server_for_profile(profile: str, server_name: str, data_dir: str | None = None) -> list[str]:
    if (get_mcp_dir(data_dir) / "access.json").exists():
        raise PermissionError("desktop_owner_required")
    path, config = _load_profile(profile, data_dir)
    enabled = get_profile_mcp_servers(profile, data_dir)
    if server_name not in enabled:
        enabled.append(server_name)
    config["mcp_servers"] = enabled
    _save_profile(path, config)
    return enabled


def disable_mcp_server_for_profile(profile: str, server_name: str, data_dir: str | None = None) -> list[str]:
    if (get_mcp_dir(data_dir) / "access.json").exists():
        raise PermissionError("desktop_owner_required")
    path, config = _load_profile(profile, data_dir)
    enabled = [name for name in get_profile_mcp_servers(profile, data_dir) if name != server_name]
    config["mcp_servers"] = enabled
    _save_profile(path, config)
    return enabled


def set_mcp_server_global_disabled(
    server_name: str,
    disabled: bool,
    data_dir: str | None = None,
) -> dict[str, Any]:
    registry = load_mcp_registry(data_dir)
    servers = registry.setdefault("mcpServers", {})
    entry = servers.get(server_name)
    if not isinstance(entry, dict):
        raise KeyError(f"MCP server is not registered: {server_name}")
    if disabled:
        entry["disabled"] = True
    else:
        entry.pop("disabled", None)
    save_mcp_registry(registry, data_dir)
    return entry


def mcp_enable_answer_approved(response: Any) -> bool:
    """Accept only the affirmative option from the explicit MCP decision."""
    if not isinstance(response, dict) or response.get('cancelled'):
        return False
    answers = response.get('answers')
    answer = answers.get('mcp_enable_decision') if isinstance(answers, dict) else None
    return isinstance(answer, dict) and answer.get('selected_options') == ['Включить']


def accept_mcp_enable_suggestion(
    profile: str,
    server_name: str,
    data_dir: str | None = None,
) -> dict[str, Any]:
    if (get_mcp_dir(data_dir) / "access.json").exists():
        raise PermissionError("desktop_owner_required")
    entry = set_mcp_server_global_disabled(server_name, False, data_dir)
    enabled = enable_mcp_server_for_profile(profile, server_name, data_dir)
    return {
        "profile": profile,
        "server_name": server_name,
        "enabled_for_profile": enabled,
        "server": entry,
    }


def list_mcp_capabilities_for_profile(
    profile: str,
    data_dir: str | None = None,
    *,
    enabled: bool | None = None,
) -> list[dict[str, Any]]:
    enabled_names = set(get_profile_mcp_servers(profile, data_dir))
    registry = list_mcp_servers(data_dir)
    result: list[dict[str, Any]] = []
    for name in sorted(registry):
        entry = registry.get(name)
        if not isinstance(entry, dict):
            continue
        is_enabled = name in enabled_names and entry.get("disabled") is not True
        if enabled is not None and is_enabled is not enabled:
            continue
        result.append(
            {
                "name": name,
                "enabled": is_enabled,
                "disabled": bool(entry.get("disabled") is True),
                "description": str(entry.get("description") or "").strip(),
                "capabilities": [
                    str(item).strip()
                    for item in entry.get("capabilities") or []
                    if str(item).strip()
                ],
                "tools_hint": [
                    str(item).strip()
                    for item in entry.get("tools_hint") or []
                    if str(item).strip()
                ],
                "requires_env": sorted(
                    str(key).strip()
                    for key in (entry.get("env") or {}).keys()
                    if str(key).strip()
                ),
                "type": str(entry.get("type") or "").strip(),
            }
        )
    return result


def materialize_profile_mcp_config(profile: str, data_dir: str | None = None) -> Path | None:
    _, config = _load_profile(profile, data_dir)
    if profile_backend_name(config) != "claude":
        return None
    enabled = get_profile_mcp_servers(profile, data_dir)
    if not enabled:
        return None
    registry = list_mcp_servers(data_dir)
    selected: dict[str, Any] = {}
    for name in enabled:
        entry = registry.get(name)
        if not isinstance(entry, dict):
            continue
        if entry.get("disabled") is True:
            continue
        selected[name] = entry
    if not selected:
        return None
    out_dir = get_mcp_dir(data_dir) / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{profile}.json"
    out_path.write_text(json.dumps({"mcpServers": selected}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def _codex_mcp_list_names(*, codex_bin: str | None = None, codex_env: dict[str, str] | None = None, timeout_s: int = 30) -> set[str]:
    binary = codex_bin or resolve_codex_bin()
    env = os.environ.copy()
    if codex_env:
        env.update({str(key): str(value) for key, value in codex_env.items()})
    proc = subprocess.run(
        [binary, "mcp", "list", "--json"],
        text=True,
        capture_output=True,
        env=env,
        timeout=max(5, int(timeout_s)),
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"codex mcp list failed: {message or f'exit {proc.returncode}'}")

    text = (proc.stdout or "").strip() or "[]"
    data = json.loads(text)
    names: set[str] = set()

    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.strip():
                names.add(item.strip())
                continue
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.add(name)
    elif isinstance(data, dict):
        for key in data.keys():
            name = str(key).strip()
            if name:
                names.add(name)

    return names


def _codex_add_command_for_server(name: str, entry: dict[str, Any], *, codex_bin: str | None = None) -> list[str]:
    binary = codex_bin or resolve_codex_bin()
    server_type = str(entry.get("type") or "").strip().lower()

    if server_type == "http":
        url = str(entry.get("url") or "").strip()
        if not url:
            raise ValueError(f"MCP server {name!r} has empty http url")
        cmd = [binary, "mcp", "add", name, "--url", url]
        bearer_token_env_var = str(entry.get("bearer_token_env_var") or "").strip()
        if bearer_token_env_var:
            cmd.extend(["--bearer-token-env-var", bearer_token_env_var])
        return cmd

    if server_type == "stdio":
        command = str(entry.get("command") or "").strip()
        if not command:
            raise ValueError(f"MCP server {name!r} has empty stdio command")
        cmd = [binary, "mcp", "add", name]
        env = entry.get("env")
        if isinstance(env, dict):
            for key, value in env.items():
                k = str(key).strip()
                if not k:
                    continue
                cmd.extend(["--env", f"{k}={value}"])
        args = entry.get("args")
        resolved_args: list[str] = []
        if isinstance(args, list):
            resolved_args = [str(arg) for arg in args]
        cmd.extend(["--", command, *resolved_args])
        return cmd

    raise ValueError(f"MCP server {name!r} has unsupported type: {server_type!r}")


def sync_codex_mcp_for_profile(
    profile: str,
    data_dir: str | None = None,
    codex_bin: str | None = None,
    codex_env: dict[str, str] | None = None,
    timeout_s: int = 30,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def emit(message: str) -> None:
        if callable(progress_cb):
            progress_cb(message)

    _, config = _load_profile(profile, data_dir)
    if profile_backend_name(config) != "codex":
        return {
            "status": "skipped",
            "profile": profile,
            "reason": "backend_not_codex",
            "added": [],
            "skipped_existing": [],
            "errors": [],
        }

    enabled = get_profile_mcp_servers(profile, data_dir)
    if not enabled:
        return {
            "status": "ok",
            "profile": profile,
            "enabled": [],
            "added": [],
            "skipped_existing": [],
            "errors": [],
        }

    registry = list_mcp_servers(data_dir)
    emit(f"mcp sync-codex: profile={profile} enabled={len(enabled)} timeout_s={timeout_s}")
    existing = _codex_mcp_list_names(codex_bin=codex_bin, codex_env=codex_env, timeout_s=timeout_s)

    added: list[str] = []
    skipped_existing: list[str] = []
    errors: list[dict[str, str]] = []

    for name in enabled:
        emit(f"mcp sync-codex: checking {name}...")
        entry = registry.get(name)
        if not isinstance(entry, dict):
            emit(f"mcp sync-codex: {name} -> missing_in_registry")
            errors.append({"name": name, "error": "missing_in_registry"})
            continue
        if entry.get("disabled") is True:
            emit(f"mcp sync-codex: {name} -> disabled_in_registry")
            errors.append({"name": name, "error": "disabled_in_registry"})
            continue
        if name in existing:
            emit(f"mcp sync-codex: {name} -> already_exists")
            skipped_existing.append(name)
            continue

        try:
            cmd = _codex_add_command_for_server(name, entry, codex_bin=codex_bin)
        except Exception as exc:
            emit(f"mcp sync-codex: {name} -> build_error: {exc}")
            errors.append({"name": name, "error": str(exc)})
            continue

        emit(f"mcp sync-codex: {name} -> adding...")
        try:
            env = os.environ.copy()
            if codex_env:
                env.update({str(key): str(value) for key, value in codex_env.items()})
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=max(5, int(timeout_s)),
                env=env,
            )
        except subprocess.TimeoutExpired:
            emit(f"mcp sync-codex: {name} -> timeout")
            errors.append({"name": name, "error": f"timeout_after_{int(timeout_s)}s"})
            continue

        if proc.returncode == 0:
            emit(f"mcp sync-codex: {name} -> added")
            added.append(name)
            existing.add(name)
            continue

        message = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        emit(f"mcp sync-codex: {name} -> error: {message}")
        errors.append({"name": name, "error": message})

    status = "ok" if not errors else ("partial" if added or skipped_existing else "error")
    emit(
        "mcp sync-codex: done "
        f"status={status} added={len(added)} "
        f"existing={len(skipped_existing)} errors={len(errors)}"
    )
    return {
        "status": status,
        "profile": profile,
        "enabled": enabled,
        "added": added,
        "skipped_existing": skipped_existing,
        "errors": errors,
    }
