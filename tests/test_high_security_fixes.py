import io
import os
import shlex
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from factory.core import status_line
from factory.core.isolation import egress_auth


@pytest.mark.parametrize("target", [
    ".attacker.example/collect", "@attacker.example/collect", "https://attacker.example/collect",
    "//attacker.example/collect", "///attacker.example/collect", "*",
    "/\\attacker.example/collect", "/path#fragment",
])
def test_proxy_rejects_target_before_requesting_credentials(target):
    with pytest.raises(ValueError):
        egress_auth._upstream_url('https://inference.runanywhere.ai', target)
    token = Mock(return_value="test-host-token")
    outbound = Mock(return_value=Response())
    proxy = egress_auth.EgressAuthProxy(egress_auth.ProxyConfig(
        upstream_base="https://inference.runanywhere.ai", token_provider=token,
    ))
    with patch.object(egress_auth, "_OPENER", SimpleNamespace(open=outbound)):
        proxy.start()
        try:
            authenticated_target = '/' + proxy.capability + target if target.startswith('/') else target
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=3) as client:
                client.sendall(f"GET {authenticated_target} HTTP/1.1\r\nHost: proxy\r\nConnection: close\r\n\r\n".encode())
                response = client.recv(4096)
            assert response.startswith(b"HTTP/1.1 400" if target.startswith('/') else b"HTTP/1.1 401"), response
            token.assert_not_called()
            outbound.assert_not_called()
        finally:
            proxy.stop()


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self):
        super().__init__(b'{"ok":true}')


@pytest.mark.parametrize("base,target,expected", [
    ("https://provider.example", "/v1/models?limit=2", "https://provider.example/v1/models?limit=2"),
    ("https://provider.example/api/anthropic/", "/v1/messages", "https://provider.example/api/anthropic/v1/messages"),
    ("http://127.0.0.1:1234/v1", "/chat/completions", "http://127.0.0.1:1234/v1/chat/completions"),
    ("https://provider.example", "/%2f%2fattacker.example/x?u=https://other.example", "https://provider.example/%2f%2fattacker.example/x?u=https://other.example"),
])
def test_proxy_preserves_origin_and_legitimate_path(base, target, expected):
    token = Mock(return_value="test-host-token")
    outbound = Mock(return_value=Response())
    proxy = egress_auth.EgressAuthProxy(egress_auth.ProxyConfig(
        upstream_base=base, token_provider=token, auth_header="x-api-key", auth_prefix="",
    ))
    with patch.object(egress_auth, "_OPENER", SimpleNamespace(open=outbound)):
        proxy.start()
        try:
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=3) as client:
                client.sendall(f"POST /{proxy.capability}{target} HTTP/1.1\r\nHost: proxy\r\nContent-Length: 2\r\nx-api-key: guest\r\nAuthorization: guest\r\n\r\n{{}}".encode())
                received = b""
                while chunk := client.recv(4096):
                    received += chunk
            assert received.startswith(b"HTTP/1.1 200")
            assert received.endswith(b'{"ok":true}')
            request = outbound.call_args.args[0]
            assert request.full_url == expected
            assert request.data == b"{}"
            assert request.get_header("X-api-key") == "test-host-token"
            assert request.get_header("Authorization") is None
            token.assert_called_once_with()
        finally:
            proxy.stop()


def git(root, *args):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    return subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "file.txt").write_text("original\n")
    git(tmp_path, "add", "file.txt")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", "Create security fixture")
    return tmp_path


@pytest.mark.parametrize("mode", ["local", "include", "environment"])
def test_status_line_never_runs_repository_fsmonitor(repository, mode):
    marker = repository / "host-canary"
    hook = repository / "fsmonitor-hook"
    hook.write_text("#!/bin/sh\nprintf invoked > " + shlex.quote(str(marker)) + "\nprintf '\\0'\n")
    hook.chmod(0o700)
    env = {}
    if mode == "include":
        extra = repository / "included.config"
        extra.write_text("[core]\n\tfsmonitor = " + str(hook) + "\n")
        git(repository, "config", "include.path", str(extra))
    elif mode == "environment":
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": str(hook)}
    else:
        git(repository, "config", "core.fsmonitor", str(hook))
    with patch.dict(os.environ, env):
        result = status_line.git_status(str(repository))
    assert not marker.exists(), "fsmonitor executed on the host"
    assert result["branch"] == "main"
    assert result["root"] == str(repository)
    assert result["is_clean"] is False


def test_status_line_preserves_clean_dirty_and_non_repository(repository, tmp_path_factory):
    clean = status_line.git_status(str(repository))
    assert clean["is_clean"] is True
    assert clean["branch"] == "main"
    assert clean["commit"] == git(repository, "rev-parse", "--short", "HEAD")
    (repository / "file.txt").write_text("changed\n")
    assert status_line.git_status(str(repository))["is_clean"] is False
    assert status_line.git_status(str(tmp_path_factory.mktemp("no-git")))["is_clean"] is None


