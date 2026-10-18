"""Own the existing local bridge and Web UI for a desktop first run."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent / 'packages')]
from socket_bootstrap import activate
activate(Path(__file__).resolve().parent)

import contextlib
import json
import os
import signal
import ssl
import sys
import threading
from importlib.metadata import version
from pathlib import Path

# Dependencies live beside the standalone interpreter, not in system Python.
sys.path.insert(0, str(Path(__file__).resolve().parent / "packages"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run(data_dir: str) -> int:
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'nt':
        from factory.core.platform_support.windows_security import protect_directory
        protect_directory(root)
    os.chmod(root, 0o700)
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True, mode=0o700)
    os.environ["FACTORY_DATA_DIR"] = str(root)
    os.environ["IGOREK_FACTORY_HOME"] = str(root)
    os.chdir(workspace)
    protocol_output = sys.stdout
    original_stderr = sys.stderr
    from runtime_logs import RuntimeLogs
    runtime_logs = RuntimeLogs(original_stderr)
    sys.stderr = runtime_logs
    sys.stdout = sys.stderr
    stop = threading.Event()
    servers = []
    threads = []
    bridge = None
    owner = None
    schedules = None
    browser_pipe = None
    opencode_pipe = None
    mobile_access = None
    protocol_lock = threading.Lock()

    def emit_protocol(frame):
        with protocol_lock:
            print(json.dumps(frame), file=protocol_output, flush=True)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    def watch_parent():
        while not stop.is_set():
            line = sys.stdin.readline(24 * 1024 * 1024 + 1)
            if not line or len(line) > 24 * 1024 * 1024:
                break
            if line.strip() == "stop":
                break
            try:
                request = json.loads(line)
            except ValueError:
                request = None
            if isinstance(request, dict) and request.get('type') == 'browser_reply':
                browser_pipe.receive(request)
                continue
            if isinstance(request, dict) and request.get('type') == 'opencode_reply':
                opencode_pipe.receive(request)
                continue
            emit_protocol(owner.dispatch(request))
        stop.set()

    try:
        # Startup imports can print diagnostics; stdout is reserved for the owner protocol.
        with contextlib.redirect_stdout(sys.stderr):
            from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge, build_server
            from factory.web_ui.server import _Server, _certificate_paths
            from vault_owner import OwnerVault
            from agent_runtime import DesktopAgentRuntime

            owner = OwnerVault(root)

            from factory.core.realtime_voice.service import VoiceService
            from provider_setup import desktop_openai_voice_key
            voice = VoiceService(key_provider=lambda: desktop_openai_voice_key(owner.lifecycle))
            bridge = MobileBridge(config=BridgeConfig(host="127.0.0.1", port=0, workdir=str(workspace)), voice=voice)
            from factory.core.mcp_tasks.service import McpTasks
            bridge.mcp_tasks = McpTasks(root=root, lifecycle=owner.lifecycle,
                consumer_valid=lambda identity: not owner.interface_locked and bool((record := bridge.store.get_device(identity)) and record.get('revoked_at') is None))
            owner.agent = DesktopAgentRuntime(root, owner.lifecycle, bridge.service, execution=owner.execution)
            owner.browser_service = bridge.service
            bridge.service.profile_control = owner.mobile_profile_control
            from factory.core.desktop_browser import DesktopBrowserPipe
            browser_pipe = DesktopBrowserPipe(emit_protocol, lambda profile: (
                not owner.interface_locked and owner.service.status()['state'] == 'unlocked'
                and owner.agent.execution.mode == 'off' and bool(owner.agent.available_profiles())
                and (profile is None or profile in owner.agent.available_profiles())))
            browser_pipe.install()
            from factory.core.opencode_tasks import OpenCodeTasks
            opencode_pipe = OpenCodeTasks(root / 'opencode-tasks', bridge.service, emit_protocol,
                lambda profile: sys.platform in {'linux', 'win32'} and browser_pipe.allowed(profile))
            opencode_pipe.install()
            bridge.service.opencode_tasks = opencode_pipe

            def poll_opencode():
                while not stop.wait(1):
                    try:
                        opencode_pipe.poll()
                    except Exception as exc:
                        print('OpenCode delegation poll: ' + type(exc).__name__, file=sys.stderr, flush=True)

            from factory.core.host_services import HostServices
            owner.host_services = HostServices(root / 'host-services', allowed=lambda profile=None: (
                not owner.interface_locked and owner.service.status()['state'] == 'unlocked'
                and owner.agent.execution.mode == 'off'
                and (profile is None or profile in owner.agent.available_profiles())))
            owner.host_services.install()
            from opencode_host_services import HostServicesMcp
            try:
                owner.opencode_services = HostServicesMcp(root / 'opencode-services', owner.host_services)
                servers.append(owner.opencode_services)
            except (OSError, ValueError):
                owner.opencode_services = None
                print('MCP локальных сервисов OpenCode недоступен; основной runtime продолжает работу.', flush=True)
            from opencode_profiles import OpenCodeProfiles
            try:
                owner.opencode_profiles = OpenCodeProfiles(root / 'opencode-profiles', owner.lifecycle,
                    lambda: not owner.interface_locked and owner.service.status()['state'] == 'unlocked')
                servers.append(owner.opencode_profiles)
            except (OSError, ValueError):
                owner.opencode_profiles = None
                print('Профили OpenCode недоступны; основной runtime продолжает работу.', flush=True)
            from factory.core.desktop_browser_preview import DesktopBrowserPreview
            bridge.service.browser_preview=DesktopBrowserPreview(browser_pipe)
            from factory.core.desktop_schedules import DesktopSchedules
            schedules = DesktopSchedules(bridge.service)
            bridge.service.schedules = schedules
            mobile = build_server(bridge, host="127.0.0.1", port=0)
            servers.append(mobile)
            bridge_url = f"http://127.0.0.1:{mobile.server_address[1]}"
            cert, key, _ = _certificate_paths()
            web = _Server(0, bridge_url)
            from provider_setup import desktop_opencode_usage
            from provider_setup import desktop_provider_usage
            web.profile_usage_provider = lambda profile, **kwargs: desktop_provider_usage(owner.lifecycle, profile, **kwargs)
            web.opencode_usage_provider = lambda **kwargs: desktop_opencode_usage(owner.lifecycle, owner.agent.status()["profile"], **kwargs)
            bridge.service.opencode_usage = web.opencode_usage_provider
            bridge.service.profile_usage = web.profile_usage_provider
            web.logs_provider = runtime_logs.tail
            web.logs_status_provider = lambda: runtime_logs.status(stop, threads, owner.agent)
            servers.append(web)
            from mobile_access import DesktopMobileAccess
            mobile_access=DesktopMobileAccess(bridge,cert,key,root)
            owner.mobile_access=mobile_access
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(str(cert), str(key))
            web.enable_tls(tls)
            bridge.start()
            schedules.start()
            for server in servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                threads.append(thread)
            pairing = bridge.store.create_pairing_code(created_by="desktop-owner")
            print('Desktop runtime запущен: web, mobile и собственное хранилище.', flush=True)
        print(json.dumps({"type": "ready", "web_port": web.server_address[1],
                          "cert": str(cert), "pairing_code": pairing["code"],
                          "factory_version": version("igorek-agent-factory")}), file=protocol_output, flush=True)
        threading.Thread(target=watch_parent, daemon=True).start()
        threading.Thread(target=poll_opencode, daemon=True).start()
        stop.wait()
        return 0
    except Exception:
        print(json.dumps({"type": "error", "code": "backend_start_failed"}), file=protocol_output, flush=True)
        return 1
    finally:
        if mobile_access:
            mobile_access.close()
        if browser_pipe:
            browser_pipe.close()
        if opencode_pipe:
            opencode_pipe.close()
        if schedules:
            schedules.stop()
        if owner:
            owner.close()
        if bridge:
            bridge.stop()
        for server, thread in zip(servers, threads):
            server.shutdown()
            thread.join(timeout=3)
        for server in servers:
            if hasattr(server, "terminals"):
                server.terminals.close_all()
            server.server_close()
        sys.stdout = protocol_output
        sys.stderr = original_stderr


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    raise SystemExit(run(sys.argv[1]))
