from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from urllib import error as urllib_error
from urllib import request as urllib_request
from pathlib import Path
from typing import Any

from .runtime import get_env_files_dir, get_profile_config
from .vault.runtime import build_services as build_vault_services

DEFAULT_LLAMA_SERVER_CANDIDATES = (
    str(Path.home() / ".local/bin/igorek-llama-server"),
    "llama-server",
)


def _stream_output(prefix: str, stream) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            print(f"[{prefix}] {line.rstrip()}")
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        loaded[key.strip()] = value.strip()
    return loaded


def _apply_env_defaults(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _resolve_builtin_gateway_preset_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "gateway.default.env"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


_WORKER_PROFILES_OFF_VALUES = {"off", "0", "no", "false", "disabled", "none"}


def _resolve_worker_profiles(active_profile: str) -> list[str]:
    """Profiles for which the gateway stack starts separate workers.

    Always serve the active runtime profile, even when its queue is empty.
    Additional profiles are explicitly listed in ``FACTORY_WORKER_PROFILES``
    using commas. Each worker consumes only its own profile's queue, so the list
    allows several queues to be served concurrently.

    Skip unknown profiles with a diagnostic instead of failing the whole stack
    on a configuration typo. ``off``/``0``/``no``/``false``/``disabled``/``none``
    disable all additional profiles.
    """
    profile = str(active_profile or "").strip()
    if not profile:
        raise ValueError("active_profile is required")
    # The active profile must exist: the stack reaches here only after
    # self-checking in run_gateway_stack, but direct calls must also fail.
    get_profile_config(profile)

    extras_raw = str(os.environ.get("FACTORY_WORKER_PROFILES") or "").strip()
    if extras_raw.lower() in _WORKER_PROFILES_OFF_VALUES:
        return [profile]

    profiles = [profile]
    seen = {profile}
    skipped: list[str] = []
    for candidate in _string_list(extras_raw):
        if candidate in seen:
            continue
        try:
            get_profile_config(candidate)
        except Exception:
            skipped.append(candidate)
            continue
        seen.add(candidate)
        profiles.append(candidate)
    if skipped:
        print(
            "[worker] FACTORY_WORKER_PROFILES: пропущены неизвестные профили: "
            + ", ".join(skipped),
            flush=True,
        )
    return profiles


def _telegram_bridge_start_reason() -> tuple[bool, str]:
    if not _truthy(os.environ.get("FACTORY_TELEGRAM_ENABLED")):
        return False, "disabled"
    if not os.environ.get("FACTORY_TELEGRAM_BOT_TOKEN", "").strip():
        return False, "missing FACTORY_TELEGRAM_BOT_TOKEN"
    return True, "ready"


def _materialize_gateway_vault_env() -> dict[str, str]:
    if not _truthy(os.environ.get("FACTORY_VAULT_ENABLED")):
        return {}
    services = _string_list(os.environ.get("FACTORY_VAULT_SERVICES"))
    if not services:
        return {}
    agent_id = str(os.environ.get("FACTORY_VAULT_AGENT") or "cc-backend").strip() or "cc-backend"
    try:
        ttl = int(str(os.environ.get("FACTORY_VAULT_TTL_SECONDS") or "900").strip() or "900")
    except Exception:
        ttl = 900
    vault_services = build_vault_services(prompt=False)
    materialized: dict[str, str] = {}
    summaries: list[str] = []
    for service in services:
        decision = vault_services.broker.get_ephemeral_env(
            service=service,
            agent_id=agent_id,
            ttl=ttl,
        )
        if not decision.allowed:
            raise RuntimeError(
                f"Igorek Vault denied service '{service}' for gateway agent '{agent_id}': {decision.reason}"
            )
        materialized.update({str(key): str(value) for key, value in decision.env.items()})
        summaries.append(f"{service}=>{','.join(sorted(decision.env.keys()))}")
    os.environ.update(materialized)
    print(
        "Gateway Vault env materialized: "
        f"agent={agent_id} services={'; '.join(summaries)}"
    )
    return materialized


def _resolve_llama_server_bin(requested: str | None = None) -> str:
    candidate = str(requested or "").strip()
    if candidate and candidate != "llama-server":
        return candidate
    for path in DEFAULT_LLAMA_SERVER_CANDIDATES:
        resolved = Path(path)
        if path == "llama-server":
            return path
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return str(resolved)
    return "llama-server"


def _wait_http_ready(base_url: str, *, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + max(5.0, timeout_s)
    while time.time() < deadline:
        for path in ("/health", "/v1/models", "/props"):
            try:
                with urllib_request.urlopen(f"{base_url}{path}", timeout=2.0) as response:
                    if int(getattr(response, "status", 200) or 200) < 500:
                        return True
            except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, ValueError):
                continue
        time.sleep(1.0)
    return False


def _resolve_session_embeddings_model_path(data_dir: str | None = None) -> str | None:
    explicit = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_MODEL_PATH", "")).strip()
    if explicit:
        resolved = Path(explicit).expanduser().resolve()
        if resolved.exists():
            return str(resolved)
        return str(resolved)
    data_root = Path(
        data_dir
        or os.environ.get("FACTORY_DATA_DIR")
        or os.environ.get("IGOREK_FACTORY_HOME")
        or Path.home() / ".igorek-agent-factory"
    ).expanduser()
    patterns = (
        "models/embeddings/jina-embeddings-v5-text-nano-retrieval*.gguf",
        "models/embeddings/jina-embeddings-v5-text-nano*.gguf",
        "models/jina-embeddings-v5-text-nano-retrieval*.gguf",
        "models/jina-embeddings-v5-text-nano*.gguf",
        "models/embeddings/*.gguf",
    )
    for pattern in patterns:
        matches = sorted(data_root.glob(pattern))
        if matches:
            return str(matches[0].resolve())
    return None


def _start_optional_session_embeddings_runtime(
    *,
    stream_output_fn,
    register_process_fn,
    host: str,
    data_dir: str | None = None,
) -> tuple[subprocess.Popen[str] | None, str | None]:
    enabled_raw = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_ENABLED", "auto")).strip().lower() or "auto"
    if enabled_raw in {"0", "false", "no", "off", "disabled"}:
        return None, None
    model_path = _resolve_session_embeddings_model_path(data_dir)
    if not model_path:
        if enabled_raw in {"1", "true", "yes", "on"}:
            print("[embeddings] warning: embeddings runtime enabled, but model path was not found; continuing without embeddings")
        return None, None

    try:
        port = int(str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_PORT", "8022")).strip() or "8022")
    except ValueError as exc:
        print(f"[embeddings] warning: invalid embeddings port: {exc}; continuing without embeddings")
        return None, None
    bind_host = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_HOST", host)).strip() or host
    model_name = str(
        os.environ.get("FACTORY_SESSION_EMBEDDINGS_MODEL", "jina-embeddings-v5-text-nano-retrieval")
    ).strip()
    gpu_layers = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_GPU_LAYERS", "999")).strip() or "999"
    ctx_size = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_CTX_SIZE", "8192")).strip() or "8192"
    extra_args = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_EXTRA_ARGS", "")).strip()
    base_url = f"http://{bind_host}:{port}"
    existing_url = str(os.environ.get("IGOREK_SESSION_EMBEDDINGS_BASE_URL", "")).strip()
    if existing_url:
        base_url = existing_url.rstrip("/")
    else:
        os.environ["IGOREK_SESSION_EMBEDDINGS_BASE_URL"] = base_url
    os.environ["IGOREK_SESSION_EMBEDDINGS_MODEL"] = model_name

    if _wait_http_ready(base_url, timeout_s=2.0):
        print(f"[embeddings] runtime already available at {base_url}")
        return None, base_url

    server_bin = _resolve_llama_server_bin(os.environ.get("FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN"))
    cmd = [
        server_bin,
        "--embeddings",
        "-m",
        model_path,
        "--host",
        bind_host,
        "--port",
        str(port),
        "-c",
        ctx_size,
        "-ngl",
        gpu_layers,
        "--pooling",
        "last",
    ]
    try:
        if extra_args:
            lexer = shlex.shlex(extra_args, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            if sys.platform == "win32":
                lexer.escape = ""
            cmd.extend(lexer)
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).resolve().parents[2]),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        print(f"[embeddings] warning: failed to start embeddings runtime: {exc}")
        return None, None

    register_process_fn(proc)
    thread = threading.Thread(target=stream_output_fn, args=("embeddings", proc.stdout), daemon=True)
    thread.start()
    if _wait_http_ready(base_url, timeout_s=35.0):
        print(f"[embeddings] runtime ready: model={model_name} base_url={base_url}")
        return proc, base_url

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    print(f"[embeddings] warning: embeddings runtime did not become ready for model={model_name}; continuing without embeddings")
    return None, None


def _resolve_telegram_bridge_profile(profile: str) -> str:
    """Pin the bridge profile to the CLI-selected one.

    Invariant (same as run_gateway_stack): the CLI --profile is authoritative;
    a stale FACTORY_TELEGRAM_PROFILE from env files must not override it,
    otherwise the bridge submits tasks for a profile no worker claims.
    """
    os.environ["FACTORY_TELEGRAM_PROFILE"] = profile
    return profile


def _recover_isolation_startup() -> None:
    """Gateway-start fail-safe pass (plan todo 25); never breaks the stack.

    Off mode is a pure no-op and prints nothing. Kvm mode reaps orphan
    domains/overlays/endpoints and marks orphaned running tasks failed through
    the isolation startup recovery; a failure in that pass is reported and the
    stack starts anyway (the host stays healthy).
    """
    try:
        from .isolation.config import MODE_KVM
        from .isolation.startup import recover_isolation_startup
    except Exception as exc:  # import boundary: the stack must still start
        print(f"[isolation] startup recovery unavailable: {exc}", flush=True)
        return
    try:
        report = recover_isolation_startup()
    except Exception as exc:  # fail-safe: the pass must never stop the gateway
        print(f"[isolation] startup recovery skipped: {exc}", flush=True)
        return
    if report.mode != MODE_KVM:
        return
    print(
        "[isolation] startup recovery: "
        f"reaped={report.reaped} failed_tasks={len(report.failed_tasks)} "
        f"error={report.error or '-'}",
        flush=True,
    )


def run_local_stack(
    api_host: str = "127.0.0.1",
    api_port: int = 8180,
    profile: str = "zai",
    poll_interval: int = 2,
) -> int:
    _bootstrap_stack_env()
    worker_profiles = _resolve_worker_profiles(profile)
    _recover_isolation_startup()
    repo_root = Path(__file__).resolve().parents[2]
    processes: list[subprocess.Popen] = []
    threads: list[threading.Thread] = []
    shutting_down = False

    def start_process(prefix: str, args: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-m", "factory.cli", *args],
            cwd=str(repo_root) if (repo_root / "factory").exists() else None,
            env={**os.environ, "PYTHONUNBUFFERED": "1", **(extra_env or {})},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        processes.append(proc)
        thread = threading.Thread(target=_stream_output, args=(prefix, proc.stdout), daemon=True)
        thread.start()
        threads.append(thread)
        return proc

    def register_external_process(proc: subprocess.Popen[str]) -> None:
        processes.append(proc)

    def cleanup() -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def shutdown(*_args) -> None:
        if shutting_down:
            return
        cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        embeddings_proc, embeddings_base_url = _start_optional_session_embeddings_runtime(
            stream_output_fn=_stream_output,
            register_process_fn=register_external_process,
            host=api_host,
            data_dir=os.environ.get("FACTORY_DATA_DIR") or os.environ.get("IGOREK_FACTORY_HOME"),
        )
        api_proc = start_process("api", ["api", "--host", api_host, "--port", str(api_port)])
        worker_procs: list[tuple[str, subprocess.Popen]] = [
            (
                worker_profile,
                start_process(
                    f"worker:{worker_profile}",
                    ["worker", "--profile", worker_profile, "--poll-interval", str(poll_interval)],
                ),
            )
            for worker_profile in worker_profiles
        ]
        telegram_should_start, telegram_skip_reason = _telegram_bridge_start_reason()
        telegram_proc: subprocess.Popen | None = None
        if telegram_should_start:
            telegram_profile = _resolve_telegram_bridge_profile(profile)
            telegram_proc = start_process(
                "telegram",
                ["telegram", "--profile", telegram_profile],
                extra_env={"FACTORY_API_BASE_URL": f"http://{api_host}:{api_port}"},
            )
        elif _truthy(os.environ.get("FACTORY_TELEGRAM_ENABLED")):
            print(f"[run] Telegram bridge not started: {telegram_skip_reason}")

        print(
            f"CLI stack started: api=http://{api_host}:{api_port} profile={profile}"
            f" workers={','.join(worker_profiles)}"
            f"{' embeddings=on' if embeddings_proc is not None or embeddings_base_url else ''}"
            f"{' telegram=on' if telegram_proc is not None else ''}. "
            "Press Ctrl+C to stop all processes."
        )

        embeddings_warned = False
        while True:
            if api_proc.poll() is not None:
                print(f"[run] API exited with code {api_proc.returncode}")
                shutdown()
            for worker_profile, worker_proc in worker_procs:
                if worker_proc.poll() is not None:
                    print(f"[run] Worker ({worker_profile}) exited with code {worker_proc.returncode}")
                    shutdown()
            if embeddings_proc is not None and embeddings_proc.poll() is not None and not embeddings_warned:
                print(f"[run] Embeddings runtime exited with code {embeddings_proc.returncode}; continuing without embeddings")
                embeddings_warned = True
                embeddings_proc = None
            if telegram_proc is not None and telegram_proc.poll() is not None:
                print(f"[run] Telegram bridge exited with code {telegram_proc.returncode}")
                shutdown()
            signal.pause()
    finally:
        cleanup()


def _resolve_gateway_env_files(explicit: str | None = None) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    search_roots: list[Path] = []
    cwd_root = Path.cwd().resolve()
    package_root = Path(__file__).resolve().parents[1]
    # Data-home env dir comes last: plaintext env files with secrets live
    # outside any repository working tree.
    for root in (cwd_root, package_root, get_env_files_dir()):
        if root not in search_roots:
            search_roots.append(root)
    existing: list[Path] = []
    seen: set[Path] = set()
    for root in search_roots:
        for name in (".env", ".env.gateway", ".env.local"):
            path = root / name
            if path.exists() and path not in seen:
                existing.append(path)
                seen.add(path)
    return existing or [cwd_root / ".env.gateway"]


def _bootstrap_stack_env(*, env_file: str | None = None) -> None:
    if _truthy(os.environ.get("FACTORY_STACK_ENV_BOOTSTRAPPED")):
        return
    explicit_keys = set(os.environ)
    builtin_values = _load_env_file(_resolve_builtin_gateway_preset_path())
    if builtin_values:
        _apply_env_defaults(builtin_values)
    merged_file_values: dict[str, str] = {}
    for env_path in _resolve_gateway_env_files(env_file):
        file_values = _load_env_file(env_path)
        if file_values:
            merged_file_values.update(file_values)
    # Explicit environment wins over files, which override built-in defaults.
    for key, value in merged_file_values.items():
        if key not in explicit_keys:
            os.environ[key] = value
    _materialize_gateway_vault_env()
    os.environ["FACTORY_STACK_ENV_BOOTSTRAPPED"] = "true"


def run_gateway_stack(
    *,
    api_host: str = "127.0.0.1",
    api_port: int = 8181,
    profile: str = "codex",
    poll_interval: int = 2,
    env_file: str | None = None,
    data_dir: str | None = None,
) -> int:
    if data_dir:
        os.environ["FACTORY_DATA_DIR"] = data_dir
        os.environ["IGOREK_FACTORY_HOME"] = data_dir
    builtin_preset_path = _resolve_builtin_gateway_preset_path()
    env_paths = _resolve_gateway_env_files(env_file)
    _bootstrap_stack_env(env_file=env_file)
    _apply_env_defaults(
        {
            "FACTORY_PORT": str(api_port),
            "FACTORY_API_PORT": str(api_port),
            "FACTORY_API_BASE_URL": f"http://127.0.0.1:{api_port}",
        }
    )
    os.environ["FACTORY_TELEGRAM_PROFILE"] = profile
    os.environ["FACTORY_BOLTBOOK_AUTONOMOUS_PROFILE"] = profile
    os.environ["FACTORY_BOLTBOOK_AUTONOMOUS_POST_PROFILE"] = profile
    try:
        get_profile_config(profile)
    except Exception as exc:
        data_root = os.environ.get("FACTORY_DATA_DIR") or os.environ.get("IGOREK_FACTORY_HOME") or "~/.igorek-agent-factory"
        raise RuntimeError(
            f"Gateway profile '{profile}' is not ready: {exc}. "
            f"Run `igorek-factory init --backend codex --data-dir {data_root}` first."
        ) from exc

    telegram_enabled = os.environ.get("FACTORY_TELEGRAM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    embeddings_enabled = str(os.environ.get("FACTORY_SESSION_EMBEDDINGS_ENABLED", "auto")).strip().lower() or "auto"
    boltbook_enabled = (
        os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    )
    effective_data_dir = os.environ.get("FACTORY_DATA_DIR") or os.environ.get("IGOREK_FACTORY_HOME") or "-"
    tui_command = f"igorek-factory chat --profile {profile}"
    if data_dir:
        tui_command += f" --data-dir {data_dir}"

    print(
        "Gateway preset:"
        f" built_in={builtin_preset_path}"
        f" env_files={','.join(str(path) for path in env_paths)}"
        f" profile={profile}"
        f" port={api_port}"
        f" embeddings={embeddings_enabled}"
        f" telegram={'on' if telegram_enabled else 'off'}"
        f" boltbook={'on' if boltbook_enabled else 'off'}"
        f" data_dir={effective_data_dir}"
    )
    print(f"Gateway TUI: {tui_command}")

    return run_local_stack(
        api_host=api_host,
        api_port=api_port,
        profile=profile,
        poll_interval=poll_interval,
    )
