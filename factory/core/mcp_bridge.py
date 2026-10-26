"""Lightweight MCP client bridge for cc-backend.

Connects to MCP servers via stdio JSON-RPC, discovers their tools,
and registers them in the cc-backend tool registry. The stdio transport
discovers tools during startup and reload without exposing host credentials.

Configuration via environment variables:
    MCP_SERVERS=fal_image,xsearch
    MCP_{NAME}_COMMAND=python3
    MCP_{NAME}_ARGS=factory/mcp_servers/fal_image_server.py
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from factory.core.tool_registry import registry


def _load_dotenv() -> None:
    """Load environment from .env files (repo-local first, then the data home)."""
    from .runtime import get_env_files_dir

    base_dir = Path(__file__).resolve().parents[1]
    env_files_dir = get_env_files_dir()
    for env_path in (
        base_dir / "config" / "gateway.default.env",
        base_dir / ".env.gateway",
        base_dir / ".env",
        base_dir / ".env.local",
        env_files_dir / ".env.gateway",
        env_files_dir / ".env",
        env_files_dir / ".env.local",
    ):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


_load_dotenv()

_MCP_CLIENTS: list["MCPClient"] = []
_MCP_LOCK = threading.Lock()
_MCP_TOOL_OWNERS: dict[str, Any] = {}
_DESKTOP_ACCESS_CHECK = None
_DESKTOP_REGISTRY = None
_DESKTOP_CALENDAR = None

def set_desktop_access_check(check, registry_provider=None, calendar_connection=None):
    global _DESKTOP_ACCESS_CHECK, _DESKTOP_REGISTRY, _DESKTOP_CALENDAR
    _DESKTOP_ACCESS_CHECK = check
    _DESKTOP_REGISTRY = registry_provider
    _DESKTOP_CALENDAR = calendar_connection


def desktop_mcp_entries():
    """Expose supported owner-managed adapters to the standard desktop worker."""
    if _DESKTOP_REGISTRY is None:
        return {}
    entries = _DESKTOP_REGISTRY()
    return {name:entries[name] for name in ('openai_docs','google_calendar') if isinstance(entries.get(name),dict)}


def calendar_approval_context(task_id,session_id,event_id=''):
    with _MCP_LOCK:
        client=next((item for item in _MCP_CLIENTS if isinstance(item,DesktopCalendarClient) and item.connection is _DESKTOP_CALENDAR),None)
    if client is None or not client.available():return {}
    context=client.contexts.get((str(session_id or ''),str(task_id or '')))
    if context is None:return {}
    return {'calendar':getattr(context,'last_calendar_info',{}),'event':context.observed_events.get(event_id,{})}


class DesktopCalendarClient:
    name = 'google_calendar'
    env = {}

    def __init__(self,connection):
        self.connection=connection
        self.contexts={}
        from .mcp_tasks.calendar import CalendarClient
        self.tools=CalendarClient(connection,self.available,lambda _:False).tools

    def available(self):
        try:
            return self.connection is _DESKTOP_CALENDAR and _DESKTOP_ACCESS_CHECK is not None and bool(_DESKTOP_ACCESS_CHECK(self.name)) and self.connection.status()['connected']
        except Exception:return False

    def start(self):
        if not self.available():raise PermissionError()

    def stop(self):
        self.contexts.clear()

    def make_handler(self,name):
        def handler(args,**kwargs):
            if not self.available():return json.dumps({'error':'mcp_access_denied'})
            from .mcp_tasks.calendar import CalendarClient
            task=str(kwargs.get('task_id') or '')
            key=(str(kwargs.get('current_session') or ''),task)
            binding=self.connection.binding_digest()
            granted=False
            if name in {'calendar_create_event','calendar_move_event'}:
                approve=kwargs.get('mcp_write_approval')
                granted=bool(callable(approve) and approve(name,args,str(kwargs.get('tool_use_id') or '')))
                if not granted:return json.dumps({'error':'calendar_approval_required','changed':False})
                previous=self.contexts.get(key)
                if previous is None or not getattr(previous,'last_calendar_info',None):return json.dumps({'error':'calendar_info_required','changed':False})
                if previous.connection_binding!=binding:return json.dumps({'error':'calendar_connection_changed','changed':False})
            if key not in self.contexts or self.contexts[key].connection_binding!=binding:
                if len(self.contexts)>=32:self.contexts.pop(next(iter(self.contexts)))
                self.contexts[key]=CalendarClient(self.connection,self.available,lambda _:False)
                self.contexts[key].connection_binding=binding
            client=self.contexts[key]
            client.approve=lambda _:granted
            import hashlib
            client.event_id_factory=lambda:hashlib.sha256((task+'\0'+str(kwargs.get('tool_use_id') or '')).encode()).hexdigest()
            try:return client.call_tool(name,args)
            except PermissionError:return json.dumps({'error':'mcp_access_denied','changed':False})
            except Exception:return json.dumps({'error':'calendar_request_failed','changed':False})
        return handler


class DesktopDocsClient:
    name = 'openai_docs'
    env = {}

    def __init__(self):
        from .mcp_tasks.remote import RemoteMcpClient
        self.remote = RemoteMcpClient(self.available)

    def available(self):
        try:
            return _DESKTOP_ACCESS_CHECK is not None and bool(_DESKTOP_ACCESS_CHECK(self.name))
        except Exception:
            return False

    @property
    def tools(self):
        return self.remote.tools

    def start(self):
        self.remote.start()

    def stop(self):
        self.remote.stop()

    def make_handler(self, tool_name):
        def handler(args, **kwargs):
            try:
                return self.remote.call_tool(tool_name, args)
            except PermissionError:
                return json.dumps({'error': 'mcp_access_denied'})
            except Exception:
                return json.dumps({'error': 'docs_request_failed'})
        return handler

_MCP_SERVER_ENV_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "tavily": ("TAVILY_API_KEY", "FACTORY_TAVILY_API_KEY"),
    "xsearch": ("XSEARCH_BEARER_TOKEN",),
    "fal_image": ("FAL_KEY", "FAL_API_KEY"),
}


def _mcp_server_available(name: str, env: dict[str, str] | None = None) -> bool:
    required_any = _MCP_SERVER_ENV_REQUIREMENTS.get(name.strip().lower())
    if not required_any:
        return True
    source = env if env is not None else os.environ
    return any(str(source.get(key, "")).strip() for key in required_any)


def mcp_server_env_requirements(name: str) -> tuple[str, ...]:
    """Env keys that satisfy this server's credential check (isolation placement).

    Public seam for plan todo 31: the host collector decides whether a server's
    credential is available before it places the server in the guest, and a
    missing credential is surfaced as a visible status.
    """
    return _MCP_SERVER_ENV_REQUIREMENTS.get(name.strip().lower(), ())


class MCPClient:
    """Manages a single MCP server subprocess (stdio transport)."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        access_check=None,
        inherit_environment=True,
    ) -> None:
        self.name = name
        self.access_check = access_check
        self.inherit_environment = inherit_environment
        self.command = command
        self.args = args or []
        self.extra_env = extra_env or {}
        self.env: dict[str, str] = {}
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._tools: list[dict[str, Any]] = []

    def start(self) -> None:
        if (self.access_check or _DESKTOP_ACCESS_CHECK) is not None and not (self.access_check or _DESKTOP_ACCESS_CHECK)(self.name):
            raise PermissionError("mcp_access_denied")
        """Launch the subprocess and perform MCP handshake."""
        # Use direct connection - strip proxy variables
        env = {k: v for k, v in os.environ.items() if "PROXY" not in k.upper()}
        if not self.inherit_environment:
            env = {k: v for k, v in env.items() if k in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT'}}
        env.update(self.extra_env)
        self.env = dict(env)
        argv = [self.command, *self.args]
        from .isolation.guest_bwrap import guest_mode_requested
        if guest_mode_requested():
            from .isolation.guest_process import unprivileged_argv
            argv = unprivileged_argv(argv)
        self._process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # MCP handshake
        self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "igorek-cc-backend", "version": "0.1.0"},
            },
        )
        self._send_notification("notifications/initialized")
        # Discover tools
        result = self._send_request("tools/list", {})
        if isinstance(result, dict):
            tools = result.get("tools")
            if isinstance(tools, list):
                self._tools = tools

    def stop(self) -> None:
        """Terminate the subprocess and release its stdio pipes."""
        process = self._process
        if process is None:
            return
        # Detach first so a concurrent/late stop() is a no-op.
        self._process = None
        # Close stdin to signal EOF, then terminate and reap before touching
        # stdout/stderr (their readers may still be draining subprocess output).
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server and return the result as a string."""
        check = self.access_check or _DESKTOP_ACCESS_CHECK
        if check is not None:
            try:
                allowed = check(self.name)
            except Exception:
                allowed = False
            if not allowed:
                return json.dumps({"error": "mcp_access_denied"})
        result = self._send_request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )
        if check is not None:
            try:
                allowed = check(self.name)
            except Exception:
                allowed = False
            if not allowed:
                return json.dumps({"error": "mcp_access_denied"})
        if isinstance(result, dict):
            # Extract text content from MCP response
            content = result.get("content")
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(str(block.get("text", "")))
                if texts:
                    return "\n".join(texts)
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    def make_handler(self, tool_name: str):
        """Create a handler callable for the tool registry."""

        def _handler(args: dict[str, Any], **kwargs: Any) -> str:
            if tool_name in {'fal_generate_image', 'fal_edit_image'}:
                from .mcp_file_tools import run_fal_file_tool
                try:
                    return run_fal_file_tool(self, tool_name, args,
                        workdir=str(kwargs.get('workdir') or ''),
                        filesystem_mode=str(kwargs.get('filesystem_mode') or 'workspace'))
                except (OSError, ValueError):
                    return json.dumps({'error': 'mcp_file_scope_denied'})
            # Inject workdir as output_dir if not provided
            workdir = kwargs.get("workdir", "")
            if workdir and "output_dir" not in args:
                args = dict(args)
                args["output_dir"] = workdir
            return self.call_tool(tool_name, args)

        return _handler

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request(self, method: str, params: dict[str, Any]) -> Any:
        assert self._process is not None
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        request_id = self._next_id()
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self._process.stdin.flush()
        # Read response line
        raw_line = self._process.stdout.readline()
        if not raw_line:
            raise RuntimeError(f"MCP server '{self.name}' closed stdout")
        response = json.loads(raw_line.decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError(f"MCP server '{self.name}' returned an invalid response")
        if "error" in response:
            error = response["error"]
            message = error.get("message", error) if isinstance(error, dict) else error
            raise RuntimeError(f"MCP error from '{self.name}': {message}")
        return response.get("result")

    def _send_notification(self, method: str) -> None:
        assert self._process is not None
        assert self._process.stdin is not None
        message = {"jsonrpc": "2.0", "method": method}
        self._process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self._process.stdin.flush()


def _convert_mcp_schema_to_anthropic(mcp_tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP tool definition to Anthropic tool-use schema format."""
    return {
        "name": mcp_tool.get("name", ""),
        "description": mcp_tool.get("description", ""),
        "input_schema": mcp_tool.get(
            "inputSchema", {"type": "object", "properties": {}}
        ),
    }


def _load_mcp_config() -> list[tuple[str, str, list[str], dict[str, str]]]:
    """Parse MCP_SERVERS and per-server env variables.

    Returns list of (name, command, args, extra_env) tuples.
    """
    servers_str = (os.environ.get("MCP_SERVERS") or "").strip()
    if not servers_str:
        return []
    configs = []
    for name in servers_str.split(","):
        name = name.strip()
        if not name:
            continue
        upper = name.upper().replace("-", "_")
        command = (os.environ.get(f"MCP_{upper}_COMMAND") or "").strip()
        if not command:
            continue
        args_str = (os.environ.get(f"MCP_{upper}_ARGS") or "").strip()
        args = args_str.split() if args_str else []
        # Collect extra env vars: MCP_{NAME}_ENV_{KEY} -> KEY
        extra_env: dict[str, str] = {}
        prefix = f"MCP_{upper}_ENV_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                target_key = key[len(prefix) :]
                extra_env[target_key] = value
        configs.append((name, command, args, extra_env))
    return configs


def _register_server_tools(client: MCPClient) -> None:
    """Register all discovered tools from an MCP client into the tool registry."""
    server_env = dict(getattr(client, "env", {}) or {})
    check_fn = getattr(client, 'available', None) or (lambda name=client.name, env=server_env: _mcp_server_available(name, env))
    discovered = [(str(tool.get("name", "")).strip(), tool) for tool in client.tools]
    with _MCP_LOCK:
        seen = set()
        for name, _ in discovered:
            if not name:
                continue
            owner = _MCP_TOOL_OWNERS.get(name)
            if name in seen or (registry.has_tool(name) and
                                (owner is None or owner[0].name != client.name or
                                 registry.get_toolset_for_tool(name) != 'mcp')):
                raise ValueError(f"MCP tool name collision: {name}")
            seen.add(name)
        for tool_name, mcp_tool in discovered:
            if not tool_name:
                continue
            schema = _convert_mcp_schema_to_anthropic(mcp_tool)
            handler = client.make_handler(tool_name)

            def guarded(args, _name=tool_name, _client=client, _handler=handler, **kwargs):
                with _MCP_LOCK:
                    owner = _MCP_TOOL_OWNERS.get(_name)
                    active = owner is not None and owner[0] is _client and registry.get_toolset_for_tool(_name) == 'mcp'
                if not active:
                    return json.dumps({'error': 'mcp_access_denied'})
                result = _handler(args, **kwargs)
                with _MCP_LOCK:
                    if _MCP_TOOL_OWNERS.get(_name) is not owner:
                        return json.dumps({'error': 'mcp_access_denied'})
                return result

            registry.register(
                name=tool_name, toolset="mcp", schema=schema,
                handler=guarded, check_fn=check_fn,
                description=str(mcp_tool.get("description", "") or f"MCP tool: {tool_name}"),
            )
            _MCP_TOOL_OWNERS[tool_name] = (client, guarded)


def _deregister_client_tools(client) -> None:
    with _MCP_LOCK:
        for name, (owner, handler) in list(_MCP_TOOL_OWNERS.items()):
            if owner is client:
                registry.deregister(name, expected_handler=handler)
                _MCP_TOOL_OWNERS.pop(name, None)


def _stop_clients(clients: list["MCPClient"]) -> None:
    """Stop clients outside the global lock: stop() may wait up to 5s."""
    for client in clients:
        _deregister_client_tools(client)
        try:
            client.stop()
        except Exception:
            pass


def _evict_clients_by_name(server_name: str) -> list["MCPClient"]:
    """Remove and return all live clients for the given server name.

    Removal happens under the lock; the caller stops the returned clients
    outside the lock so a slow terminate() never blocks other MCP calls.
    """
    victims: list[MCPClient] = []
    with _MCP_LOCK:
        keep: list[MCPClient] = []
        for client in _MCP_CLIENTS:
            if client.name == server_name:
                victims.append(client)
            else:
                keep.append(client)
        if victims:
            _MCP_CLIENTS[:] = keep
    return victims


def _stop_and_evict_clients(server_name: str) -> None:
    """Stop and remove all live clients for the given server name.

    Invariant: at most one live subprocess per MCP server name in this process.
    Without this, per-task hot reload spawned a fresh subprocess on every call
    while the previous one leaked (process + stdio pipes) until FD exhaustion.
    """
    _stop_clients(_evict_clients_by_name(server_name))


def _register_client(client: "MCPClient") -> None:
    """Register a fully started client, evicting any previous client of same name.

    The swap happens only after start()/handshake succeeded, so a failed
    reload never leaves a server without its previously working client.
    """
    victims: list[MCPClient] = []
    with _MCP_LOCK:
        keep: list[MCPClient] = []
        for existing in _MCP_CLIENTS:
            if existing is client:
                keep.append(existing)
            elif existing.name == client.name:
                victims.append(existing)
            else:
                keep.append(existing)
        keep.append(client)
        _MCP_CLIENTS[:] = keep
    _stop_clients(victims)


def reload_mcp_server_from_registry(
    server_name: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Start one registry-backed MCP server and register discovered tools."""
    name = str(server_name or "").strip()
    if not name:
        return {"status": "error", "error": "server_name is required", "tools": []}
    if not isinstance(entry, dict):
        return {"status": "error", "error": "server entry must be an object", "tools": []}
    if entry.get("disabled") is True:
        _stop_and_evict_clients(name)
        return {
            "status": "skipped",
            "reason": "server_globally_disabled",
            "server_name": name,
            "tools": [],
        }
    server_type = str(entry.get("type") or "").strip().lower()
    if server_type=='builtin' and name=='google_calendar' and entry.get('adapter')=='google_calendar':
        client=None
        try:
            if _DESKTOP_CALENDAR is None or _DESKTOP_ACCESS_CHECK is None or not _DESKTOP_ACCESS_CHECK(name):
                return {'status':'skipped','reason':'mcp_access_denied','server_name':name,'tools':[]}
            with _MCP_LOCK:
                client=next((item for item in _MCP_CLIENTS if isinstance(item,DesktopCalendarClient) and item.connection is _DESKTOP_CALENDAR),None)
            if client is None:
                client=DesktopCalendarClient(_DESKTOP_CALENDAR);client.start();_register_client(client)
            _register_server_tools(client)
            return {'status':'ok','server_name':name,'tools':[tool['name'] for tool in client.tools]}
        except Exception:
            return {'status':'error','server_name':name,'error':'calendar_connection_failed','tools':[]}
    if server_type == 'builtin' and name == 'openai_docs' and entry.get('adapter') == 'openai_docs':
        client = None
        try:
            if _DESKTOP_ACCESS_CHECK is None or not _DESKTOP_ACCESS_CHECK(name):
                return {'status': 'skipped', 'reason': 'mcp_access_denied', 'server_name': name, 'tools': []}
            with _MCP_LOCK:
                client = next((item for item in _MCP_CLIENTS if isinstance(item, DesktopDocsClient) and item.tools), None)
            if client is None:
                client = DesktopDocsClient()
                client.start()
                _register_client(client)
            _register_server_tools(client)
            tools = [tool['name'] for tool in client.tools]
            return {'status': 'ok', 'server_name': name, 'tools': tools, 'tool_count': len(tools)}
        except Exception:
            if client is not None:
                _stop_and_evict_clients(name)
                client.stop()
            return {'status': 'error', 'server_name': name, 'error': 'docs_connection_failed', 'tools': []}
    if server_type != "stdio":
        return {
            "status": "unsupported",
            "reason": "hot_reload_supports_stdio_only",
            "server_name": name,
            "type": server_type,
            "tools": [],
        }
    command = str(entry.get("command") or "").strip()
    if not command:
        return {"status": "error", "error": "stdio command is empty", "tools": []}
    raw_args = entry.get("args") if isinstance(entry.get("args"), list) else []
    args = [str(arg) for arg in raw_args]
    raw_env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    extra_env = {str(key): str(value) for key, value in raw_env.items()}
    client: MCPClient | None = None
    try:
        # Start the replacement first and evict the previous live client only
        # after a successful handshake: a failed start must not take down the
        # server that is already running.
        client = MCPClient(name, command, args, extra_env)
        client.start()
        _register_server_tools(client)
        _register_client(client)
        tools = [str(tool.get("name") or "").strip() for tool in client.tools if str(tool.get("name") or "").strip()]
        return {
            "status": "ok",
            "server_name": name,
            "tools": tools,
            "tool_count": len(tools),
        }
    except Exception as exc:
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
        return {
            "status": "error",
            "server_name": name,
            "error": f"{type(exc).__name__}: {exc}",
            "tools": [],
        }


def discover_and_register_mcp_tools() -> list[str]:
    """Public entry point: discover and register all configured MCP servers.

    Called at module import time from cc_backend_tools.py.
    Returns list of registered tool names.
    """
    configs = _load_mcp_config()
    if not configs:
        return []
    registered: list[str] = []
    for name, command, args, extra_env in configs:
        client: MCPClient | None = None
        try:
            client = MCPClient(name, command, args, extra_env)
            client.start()
            _register_server_tools(client)
            _register_client(client)
            registered.extend(t.get("name", "") for t in client.tools)
        except Exception as exc:
            if client is not None:
                try:
                    client.stop()
                except Exception:
                    pass
            import sys

            print(
                f"[mcp_bridge] failed to start MCP server '{name}': {exc}",
                file=sys.stderr,
            )
    return registered


def shutdown_mcp_servers() -> None:
    """Gracefully stop all MCP server subprocesses."""
    with _MCP_LOCK:
        clients = list(_MCP_CLIENTS)
        _MCP_CLIENTS.clear()
    _stop_clients(clients)
