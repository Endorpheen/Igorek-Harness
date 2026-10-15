"""Repository-scoped smart HTTP gateway; upstream and credentials stay host-side."""
from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import secrets
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from .git_gateway_contract import GitGatewayRoute, validate_repository_url

logger = logging.getLogger(__name__)
ENV_GIT_REPOSITORIES = "IGOREK_ISOLATION_GIT_REPOSITORIES"
MAX_BODY = 64 * 1024 * 1024


@dataclass(frozen=True)
class GitRepositoryGrant:
    url: str
    push: bool = False

    def __post_init__(self):
        validate_repository_url(self.url)
        if type(self.push) is not bool:
            raise ValueError("git_repository_push_invalid")


def repository_grants(env) -> tuple[GitRepositoryGrant, ...]:
    raw = env.get(ENV_GIT_REPOSITORIES, "")
    if not raw.strip():
        return ()
    values = json.loads(raw)
    if not isinstance(values, list):
        raise ValueError("git_repository_grants_invalid")
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) - {"url", "push"} or "url" not in value:
            raise ValueError("git_repository_grants_invalid")
        result.append(GitRepositoryGrant(value["url"], value.get("push", False)))
    if len({grant.url for grant in result}) != len(result):
        raise ValueError("git_repository_grants_duplicate")
    return tuple(result)


def grant_for_workspace(grants, workdir: str) -> GitRepositoryGrant | None:
    if not grants:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", workdir, "config", "--local", "--no-includes", "--get-regexp", r"^remote\..*\.(url|pushurl)$"],
            capture_output=True, text=True, timeout=5, check=False,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    urls = {line.split(" ", 1)[1] for line in result.stdout.splitlines() if " " in line}
    selected = [grant for grant in grants if grant.url in urls or grant.url + ".git" in urls]
    if len(selected) > 1:
        raise ValueError("git_repository_grant_ambiguous")
    return selected[0] if selected else None


def host_git_authorization(repository: str) -> str:
    """Use host credential helpers, never guest helpers or a token in the wire."""
    process = subprocess.run(
        ["git", "credential", "fill"], input="url=" + repository + "\n\n",
        capture_output=True, text=True, timeout=10, check=False,
        cwd="/", env={**{key: os.environ[key] for key in ("PATH", "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG", "LC_ALL") if key in os.environ}, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"},
    )
    if process.returncode != 0:
        return ""
    fields = dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)
    if "username" not in fields or "password" not in fields:
        return ""
    encoded = base64.b64encode((fields['username'] + ':' + fields['password']).encode()).decode()
    return "Basic " + encoded


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(*args)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()



class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def _reply(self, status: int, reason: str):
        gateway = self.server.gateway
        logger.info("git gateway session=%s repository=%s status=%s reason=%s", gateway.session_id, gateway.grant.url, status, reason)
        body = (reason + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _body(self) -> bytes:
        self.connection.settimeout(30)
        lengths = self.headers.get_all("Content-Length") or []
        transfers = self.headers.get_all("Transfer-Encoding") or []
        if len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers):
            raise ValueError("git_body_framing_invalid")
        if transfers:
            if transfers != ["chunked"]:
                raise ValueError("git_body_framing_invalid")
            body = bytearray()
            while True:
                line = self.rfile.readline(128)
                if not line.endswith(b"\r\n") or len(line) >= 128:
                    raise ValueError("git_body_framing_invalid")
                size = int(line[:-2].split(b";", 1)[0], 16)
                if size < 0 or len(body) + size > MAX_BODY:
                    raise ValueError("git_body_too_large")
                if not size:
                    if self.rfile.readline(4) != b"\r\n":
                        raise ValueError("git_body_framing_invalid")
                    return bytes(body)
                piece = self.rfile.read(size)
                if len(piece) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("git_body_framing_invalid")
                body.extend(piece)
        if len(lengths) != 1:
            raise ValueError("git_body_length_required")
        size = int(lengths[0])
        if size < 0 or size > MAX_BODY:
            raise ValueError("git_body_too_large")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("git_body_framing_invalid")
        return body

    def _forward(self):
        gateway = self.server.gateway
        if gateway.revoked.is_set():
            self._reply(403, "git_grant_revoked")
            return
        # Compare the raw path, not a decoded/normalized guest-controlled URL.
        prefix = "/" + gateway.nonce
        path = self.path
        suffix = path[len(prefix):] if path.startswith(prefix) else ""
        service = None
        if self.command == "GET" and not self.headers.get("Transfer-Encoding") and self.headers.get("Content-Length", "0") == "0":
            for candidate in ("git-upload-pack", "git-receive-pack"):
                if suffix in ("/info/refs?service=" + candidate, ".git/info/refs?service=" + candidate):
                    service = candidate
        elif self.command == "POST":
            for candidate in ("git-upload-pack", "git-receive-pack"):
                if suffix in ("/" + candidate, ".git/" + candidate) and self.headers.get("Content-Type") == "application/x-" + candidate + "-request":
                    service = candidate
        if service is None or not path.startswith(prefix):
            self._reply(403, "git_route_denied")
            return
        if service == "git-receive-pack" and not gateway.grant.push:
            self._reply(403, "git_push_not_granted")
            return
        try:
            body = self._body() if self.command == "POST" else None
        except (ValueError, OSError):
            self._reply(400, "git_body_invalid")
            return
        if gateway.revoked.is_set():
            self._reply(403, "git_grant_revoked")
            return
        parsed = urlsplit(gateway.grant.url)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/x-" + service + "-request"
        protocol = self.headers.get("Git-Protocol")
        if protocol in {"version=1", "version=2"}:
            headers["Git-Protocol"] = protocol
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port, timeout=30)
        try:
            auth = gateway.authorization(gateway.grant.url)
            if auth:
                headers["Authorization"] = auth
            repository_path = parsed.path if parsed.path.endswith(".git") else parsed.path + ".git"
            target = repository_path + ("/info/refs?service=" + service if self.command == "GET" else "/" + service)
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            expected = "application/x-" + service + ("-advertisement" if self.command == "GET" else "-result")
            if response.status != 200 or response.getheader("Content-Type", "").split(";", 1)[0] != expected:
                self._reply(502, "git_upstream_http_" + str(response.status))
                status = response.status
            else:
                self.send_response(200)
                self.send_header("Content-Type", expected)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > MAX_BODY:
                        raise ValueError("git_response_too_large")
                    self.wfile.write(chunk)
                status = 200
            logger.info("git gateway session=%s repository=%s operation=%s status=%s", gateway.session_id, gateway.grant.url, service, status)
        except (OSError, ValueError, http.client.HTTPException, subprocess.TimeoutExpired):
            if not self.close_connection:
                self._reply(502, "git_upstream_unavailable")
            logger.warning("git gateway session=%s repository=%s operation=%s unavailable", gateway.session_id, gateway.grant.url, service)
        finally:
            connection.close()


class GitGateway:
    def __init__(self, grant: GitRepositoryGrant, session_id: str, *, authorization: Callable[[str], str] = host_git_authorization):
        self.grant, self.session_id, self.authorization = grant, session_id, authorization
        self.revoked = threading.Event()
        self.nonce = secrets.token_hex(32)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.gateway = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="git-gateway")
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def route(self) -> GitGatewayRoute:
        return GitGatewayRoute(self.grant.url, f"http://10.0.2.2:{self.port}/{self.nonce}")

    def close(self):
        self.revoked.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
