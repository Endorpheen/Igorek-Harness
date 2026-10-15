"""TDD suite for MCP / Vault / skill-asset placement in kvm mode (plan todo 31).

The placement decision this suite pins:

* **MCP servers run in the guest** - the host resolves each configured server
  (env-var config and profile-enabled registry entries) and carries its program,
  argv and *ephemeral* env over the versioned task payload; the guest starts the
  same stdio servers with the same env and registers their tools, so a
  ``tools/list`` handshake inside the guest succeeds.
* **Vault materialization stays host-side** - the host materializes ephemeral
  env (``worker._materialize_vault_env``) and only the values a server needs
  travel in that server's env section; the whole data dir never crosses.
* **Skill assets are a bounded read-only export** - the host mirrors the
  task-local ``.skill_runtime/<skill>`` tree the skills manager already
  materialized (nothing else), the guest writes it byte-for-byte under its
  artifact root, and ``skills_text`` paths are rewritten host->guest.
* **A missing capability is never a silent skip** - a server whose credential is
  absent, a server marked ``host_brokered`` (the todo-19 seam), a server that
  fails to start in the guest and a skill asset that cannot be exported all
  produce a visible ``isolation.*`` trace event on the host.
* **The host-brokered seam** - servers whose credentials must stay host-side are
  marked ``host_brokered``; the guest does not start them and reports
  ``isolation.mcp_brokered_pending``. Until the broker lands (todo 19) the
  credential-bearing servers (``tavily``, ``xsearch``, ``fal_image``) run
  in-guest with their ephemeral env, and a missing credential is surfaced.

Adversarial classes covered here:

* misleading success - the asset is asserted byte-for-byte *after* the guest
  wrote it, and the MCP tool is dispatched, never just looked up by name;
* stale state - a missing MCP server degrades loudly (event), never silently;
* dirty worktree - every write stays under ``tmp_path`` (which lives under
  ``/tmp``, a guest root).
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from factory.core.isolation.channel import (
    ChannelConfig,
    ChannelMessage,
    ChannelServer,
    MessageKind,
    PeerCid,
    PeerSession,
    SessionId,
    SessionMap,
    guest_connect,
)
from factory.core.isolation.config import IsolationConfig
from factory.core.isolation.domain import DomainSpec, SessionDomain
from factory.core.isolation.artifact_map import guest_artifact_dir
from factory.core.isolation.extensions import (
    McpSources,
    GUEST_SKILLS_SUBDIR,
    MAX_SKILL_ASSET_FILE_BYTES,
    MCP_CREDENTIAL_EPHEMERAL_ENV,
    MCP_CREDENTIAL_HOST_BROKER,
    MCP_PLACEMENT_HOST_BROKERED,
    MCP_PLACEMENT_IN_GUEST,
    REASON_CREDENTIAL_MISSING,
    REASON_FILE_TOO_LARGE,
    REASON_HOST_BROKER_PENDING,
    REASON_PATH_DENIED,
    SKILL_ASSET_ENCODING_BASE64,
    SKILL_RUNTIME_SUBDIR,
    CapabilityStatus,
    McpServerPlacement,
    SkillAsset,
    TaskExtensions,
    apply_guest_mcp_servers,
    collect_task_extensions,
    emit_capability_statuses,
    guest_skills_root,
    materialize_skill_assets,
    rewrite_skill_runtime_paths,
)
from factory.core.isolation.guest_runner import GuestRunner, RunnerSeams, RunnerTuning
from factory.core.isolation.egress_enforce import ApplyResult
from factory.core.isolation.guest_task import (
    DEFAULT_GUEST_ARTIFACT_DIR,
    DEFAULT_GUEST_ROOTS,
    GuestTask,
    GuestTaskError,
)
from factory.core.isolation.history import HostTaskInput, build_wire_task_payload
from factory.core.isolation.manager import GUEST_ARTIFACT_DIR, IsolationManager, ManagerSeams, SessionVm
from factory.core.isolation.workspace import GUEST_WORKSPACE_PATH
from factory.core.tool_registry import registry
from factory.core.trace_taxonomy import CcTraceEvent

MCP_UNAVAILABLE = "isolation.mcp_unavailable"
MCP_BROKERED_PENDING = "isolation.mcp_brokered_pending"
SKILL_ASSET_UNAVAILABLE = "isolation.skill_asset_unavailable"
FAKE_TOOL = "fake_echo"


def _fake_apply_egress(ruleset: str) -> ApplyResult:
    """Egress enforcement is covered by tests/test_isolation_egress_enforce.py."""
    return ApplyResult(
        applied=True,
        verified=True,
        table="inet igorek_egress",
        ruleset_sha256="0" * 64,
        rule_count=1,
        listed_rules=1,
    )

# One stdio MCP server that answers the initialize/tools/list handshake and one
# tools/call; the marker file proves tools/list actually ran in the guest.
FAKE_MCP_SERVER_SOURCE = '''
import json
import sys
from pathlib import Path

MARKER = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def reply(request_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        reply(message["id"], {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "tools/list":
        if MARKER is not None:
            MARKER.write_text("tools/list", encoding="utf-8")
        reply(message["id"], {"tools": [{"name": "fake_echo", "description": "echo back", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]})
    elif method == "tools/call":
        text = str(message.get("params", {}).get("arguments", {}).get("text", ""))
        reply(message["id"], {"content": [{"type": "text", "text": "echo:" + text}]})
    else:
        reply(message["id"], {})
'''


def _encoded(content: bytes) -> str:
    """The only wire encoding for asset bytes (policy-safe urlsafe base64)."""
    return base64.urlsafe_b64encode(content).decode("ascii")


def _decode(asset: SkillAsset) -> bytes:
    return base64.urlsafe_b64decode(asset.content.encode("ascii"))


def _mcp_env(**overrides: str) -> dict[str, str]:
    """A minimal MCP env-var config; no host env leaks into the collector."""
    env: dict[str, str] = {
        "MCP_SERVERS": "fake",
        "MCP_FAKE_COMMAND": "python3",
        "MCP_FAKE_ARGS": "/tmp/fake/server.py --stdio",
        "MCP_FAKE_ENV_MODE": "test",
    }
    env.update(overrides)
    return env


def _write_skill(host_artifacts: Path, skill_id: str = "demo-skill") -> Path:
    runtime = host_artifacts / SKILL_RUNTIME_SUBDIR / skill_id
    (runtime / "references").mkdir(parents=True)
    (runtime / "references" / "notes.md").write_text("ASSET-CONTENT-42", encoding="utf-8")
    return runtime


def _fake_server_script(tmp_path: Path, marker: Path | None = None) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_MCP_SERVER_SOURCE, encoding="utf-8")
    return script


def _host_input(**overrides: Any) -> HostTaskInput:
    values: dict[str, Any] = {
        "task_id": "task-1",
        "profile": "cc-test",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": "system",
        "user_message": "user",
        "skills_text": "skills",
        "foundation_context": "foundation",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 512,
        "timeout_s": 30,
        "workdir": GUEST_WORKSPACE_PATH,
        "artifact_workdir": DEFAULT_GUEST_ARTIFACT_DIR,
    }
    values.update(overrides)
    return HostTaskInput(**values)


def _round_trip(wire: dict[str, Any]) -> GuestTask:
    return GuestTask.from_wire(
        json.loads(json.dumps(wire, ensure_ascii=False)),
        guest_roots=DEFAULT_GUEST_ROOTS,
    )


# ── host placement decision ──────────────────────────────────────────────────


def test_mcp_env_config_is_placed_in_the_guest_with_its_configured_env(tmp_path: Path) -> None:
    # Given: one env-var configured stdio server with a per-server env value
    # When: the host collects the task extensions
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env=_mcp_env(), registry={}, profile_servers=()),
    )

    # Then: exactly that server is placed in the guest with argv + env intact
    assert collected.statuses == ()
    assert [server.name for server in collected.mcp_servers] == ["fake"]
    server = collected.mcp_servers[0]
    assert server.placement == MCP_PLACEMENT_IN_GUEST
    assert server.credential_source == "none"
    assert server.program == "python3"
    assert server.args == ("/tmp/fake/server.py", "--stdio")
    assert server.env_map() == {"MODE": "test"}


def test_ephemeral_vault_env_travels_only_with_the_server_that_needs_it(tmp_path: Path) -> None:
    # Given: a credential-bearing server whose ephemeral key is in the host env
    env = _mcp_env(
        MCP_SERVERS="tavily",
        TAVILY_API_KEY="tvly-secret",
    )
    del env["MCP_FAKE_COMMAND"], env["MCP_FAKE_ARGS"], env["MCP_FAKE_ENV_MODE"]
    env["MCP_TAVILY_COMMAND"] = "python3"
    env["MCP_TAVILY_ARGS"] = "/tmp/tavily/server.py"

    # When: the host collects the task extensions
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env=env, registry={}, profile_servers=()),
    )

    # Then: the server is placed in-guest with the ephemeral credential carried
    # in its own env section only (never as a global guest env)
    assert collected.statuses == ()
    server = collected.mcp_servers[0]
    assert server.credential_source == MCP_CREDENTIAL_EPHEMERAL_ENV
    assert server.env_map()["TAVILY_API_KEY"] == "tvly-secret"
    assert server.env_map() == {"TAVILY_API_KEY": "tvly-secret"}


def test_missing_credential_surfaces_a_visible_status_instead_of_a_silent_skip(tmp_path: Path) -> None:
    # Given: an enabled credential-bearing server with no credential anywhere
    env = {
        "MCP_SERVERS": "tavily",
        "MCP_TAVILY_COMMAND": "python3",
        "MCP_TAVILY_ARGS": "/tmp/tavily/server.py",
    }

    # When: the host collects the task extensions
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env=env, registry={}, profile_servers=()),
    )

    # Then: the server is not placed and the missing capability is named
    assert collected.mcp_servers == ()
    assert len(collected.statuses) == 1
    status = collected.statuses[0]
    assert (status.kind, status.subject, status.reason) == (
        MCP_UNAVAILABLE,
        "tavily",
        REASON_CREDENTIAL_MISSING,
    )
    # And: the record the host writes into the trace is a taxonomy-shaped event
    record = status.to_trace_record()
    assert record["kind"] == MCP_UNAVAILABLE
    assert record["subject"] == "tavily"
    assert record["reason"] == REASON_CREDENTIAL_MISSING


def test_host_brokered_marker_keeps_the_env_host_side_and_reports_pending(tmp_path: Path) -> None:
    # Given: a server marked host_brokered (the todo-19 seam) with a secret env
    env = {
        "MCP_SERVERS": "secure",
        "MCP_SECURE_COMMAND": "python3",
        "MCP_SECURE_ARGS": "/tmp/secure/server.py",
        "MCP_SECURE_ENV_TOKEN": "host-only-secret",
        "MCP_SECURE_HOST_BROKERED": "true",
    }

    # When: the host collects the task extensions
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env=env, registry={}, profile_servers=()),
    )

    # Then: the spec travels as a broker request with no env value at all
    assert [server.name for server in collected.mcp_servers] == ["secure"]
    server = collected.mcp_servers[0]
    assert server.placement == MCP_PLACEMENT_HOST_BROKERED
    assert server.credential_source == MCP_CREDENTIAL_HOST_BROKER
    assert server.env_map() == {}


def test_profile_enabled_registry_servers_are_placed_and_disabled_ones_stay_off(tmp_path: Path) -> None:
    # Given: a registry with one enabled and one globally disabled server
    registry_payload = {
        "ctx": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "pkg"],
            "env": {"TOKEN": "t"},
        },
        "off": {"type": "stdio", "command": "npx", "args": [], "disabled": True},
    }

    # When: the host collects the extensions for that profile
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env={}, registry=registry_payload, profile_servers=("ctx", "off")),
    )

    # Then: only the enabled server is placed, matching off-mode parity
    assert [server.name for server in collected.mcp_servers] == ["ctx"]
    assert collected.mcp_servers[0].program == "npx"
    assert collected.mcp_servers[0].env_map() == {"TOKEN": "t"}
    assert collected.statuses == ()


def test_non_stdio_registry_server_is_reported_not_placed(tmp_path: Path) -> None:
    # Given: an http MCP server the stdio bridge cannot run in the guest
    registry_payload = {"remote": {"type": "http", "url": "https://example.invalid/mcp"}}

    # When: the host collects the extensions
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env={}, registry=registry_payload, profile_servers=("remote",)),
    )

    # Then: it is refused with a visible reason, never silently dropped
    assert collected.mcp_servers == ()
    assert [(status.subject, status.reason) for status in collected.statuses] == [
        ("remote", "unsupported_type")
    ]


def test_spec_the_channel_policy_would_deny_is_reported_not_placed(tmp_path: Path) -> None:
    # Given: a server whose program is a host path outside every guest root
    env = {
        "MCP_SERVERS": "hostbin",
        "MCP_HOSTBIN_COMMAND": "/home/example/bin/python3",
        "MCP_HOSTBIN_ARGS": "server.py",
    }

    # When: the host collects the extensions
    collected = collect_task_extensions(
        str(tmp_path),
        mcp=McpSources(env=env, registry={}, profile_servers=()),
    )

    # Then: it never reaches the wire and the refusal is visible
    assert collected.mcp_servers == ()
    assert len(collected.statuses) == 1
    status = collected.statuses[0]
    assert (status.kind, status.subject, status.reason) == (
        MCP_UNAVAILABLE,
        "hostbin",
        "payload_denied",
    )
    assert "payload_host_path_denied" in status.detail


def test_skill_runtime_tree_is_exported_and_nothing_outside_it(tmp_path: Path) -> None:
    # Given: a materialized skill runtime plus a decoy next to it
    host_artifacts = tmp_path / "artifacts"
    runtime = _write_skill(host_artifacts)
    package = tmp_path / "pkg" / "demo-skill" / "scripts"
    package.mkdir(parents=True)
    (package / "fetch.py").write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    os.symlink(package, runtime / "scripts", target_is_directory=True)
    (host_artifacts / "decoy.txt").write_text("must-not-travel", encoding="utf-8")

    # When: the host collects the extensions
    collected = collect_task_extensions(
        str(host_artifacts),
        skill_sources={'demo-skill': str(package.parent)},
        mcp=McpSources(env={}, registry={}, profile_servers=()),
    )

    # Then: the whole materialized tree is exported byte-for-byte (symlinks
    # included) and nothing else
    assert collected.statuses == ()
    exported = {asset.path: _decode(asset) for asset in collected.skill_assets}
    assert exported == {
        "references/notes.md": b"ASSET-CONTENT-42",
        "scripts/fetch.py": b"#!/usr/bin/env python3\nprint('hi')\n",
    }
    assert {asset.skill_id for asset in collected.skill_assets} == {"demo-skill"}
    assert {asset.encoding for asset in collected.skill_assets} == {SKILL_ASSET_ENCODING_BASE64}
    assert b"must-not-travel" not in b"".join(exported.values())


def test_oversized_asset_is_reported_and_never_carried(tmp_path: Path) -> None:
    # Given: a skill asset beyond the per-file export budget
    host_artifacts = tmp_path / "artifacts"
    runtime = _write_skill(host_artifacts)
    (runtime / "references" / "huge.bin").write_bytes(b"x" * (MAX_SKILL_ASSET_FILE_BYTES + 1))

    # When: the host collects the extensions
    collected = collect_task_extensions(
        str(host_artifacts),
        mcp=McpSources(env={}, registry={}, profile_servers=()),
    )

    # Then: the small asset still travels, the oversized one is visible, not silent
    assert [asset.path for asset in collected.skill_assets] == ["references/notes.md"]
    assert [(status.kind, status.reason) for status in collected.statuses] == [
        (SKILL_ASSET_UNAVAILABLE, REASON_FILE_TOO_LARGE)
    ]
    assert "huge.bin" in collected.statuses[0].subject


def test_skills_text_runtime_paths_are_rewritten_to_the_guest_root(tmp_path: Path) -> None:
    # Given: the skills_text the host builds with a host runtime_dir
    host_artifacts = tmp_path / "artifacts"
    skills_text = (
        "## ВЫБРАННЫЕ SKILLS ДЛЯ ЭТОЙ ЗАДАЧИ\n"
        f"runtime_dir: {host_artifacts}/{SKILL_RUNTIME_SUBDIR}/demo-skill\n"
        "bundled_files:\n- references/notes.md\n"
    )

    # When: the host rewrites it for the guest
    rewritten = rewrite_skill_runtime_paths(
        skills_text,
        str(host_artifacts),
        guest_artifact_workdir=DEFAULT_GUEST_ARTIFACT_DIR,
    )

    # Then: only the runtime root is remapped, the rest of the text is untouched
    guest_root = guest_skills_root(DEFAULT_GUEST_ARTIFACT_DIR)
    assert f"runtime_dir: {guest_root}/demo-skill" in rewritten
    assert "bundled_files:\n- references/notes.md" in rewritten
    assert str(host_artifacts) not in rewritten
    assert guest_root == f"{DEFAULT_GUEST_ARTIFACT_DIR}/{GUEST_SKILLS_SUBDIR}"


# ── guest application ────────────────────────────────────────────────────────


def test_guest_materializes_assets_byte_for_byte_under_its_artifact_root(tmp_path: Path) -> None:
    # Given: one asset as it arrives on the wire
    asset = SkillAsset(
        skill_id="demo-skill",
        path="references/notes.md",
        content=_encoded(b"ASSET-CONTENT-42"),
    )

    # When: the guest materializes it
    statuses = materialize_skill_assets([asset], str(tmp_path / "artifacts"))

    # Then: the file is readable at the documented guest path with exact bytes
    assert statuses == ()
    target = tmp_path / "artifacts" / GUEST_SKILLS_SUBDIR / "demo-skill" / "references" / "notes.md"
    assert target.read_bytes() == b"ASSET-CONTENT-42"


def test_guest_refuses_asset_paths_that_escape_the_skill_root(tmp_path: Path) -> None:
    # Given: assets whose skill id / path try to escape the export root
    escape = SkillAsset(
        skill_id="demo-skill",
        path="../escape.txt",
        content=_encoded(b"nope"),
    )
    absolute = SkillAsset(
        skill_id="demo-skill",
        path="/tmp/igorek-absolute-escape.txt",
        content=_encoded(b"nope"),
    )

    # When: the guest materializes them
    statuses = materialize_skill_assets([escape, absolute], str(tmp_path / "artifacts"))

    # Then: both are refused with a stable reason and nothing was written
    assert [status.reason for status in statuses] == [REASON_PATH_DENIED, REASON_PATH_DENIED]
    assert not (tmp_path / "artifacts" / "skills" / "escape.txt").exists()
    assert not Path("/tmp/igorek-absolute-escape.txt").exists()


def test_guest_brokered_server_yields_a_pending_status_not_a_start(tmp_path: Path) -> None:
    # Given: one host-brokered spec the guest must not start
    server = McpServerPlacement(
        name="secure",
        program="python3",
        args=("/tmp/secure/server.py",),
        placement=MCP_PLACEMENT_HOST_BROKERED,
        credential_source=MCP_CREDENTIAL_HOST_BROKER,
    )
    started: list[str] = []

    # When: the guest applies the placement
    with patch.dict(os.environ, {}, clear=False):
        statuses = apply_guest_mcp_servers(
            [server], starter=lambda spec: started.append(spec.name) or {"status": "ok"}
        )

    # Then: it was not started and the wait for the broker is visible
    assert started == []
    assert [(status.kind, status.subject, status.reason) for status in statuses] == [
        (MCP_BROKERED_PENDING, "secure", REASON_HOST_BROKER_PENDING)
    ]


def test_guest_start_failure_yields_a_visible_status(tmp_path: Path) -> None:
    # Given: an in-guest server whose starter fails
    server = McpServerPlacement(name="broken", program="python3", args=("/tmp/missing.py",))

    # When: the guest applies the placement
    with patch.dict(os.environ, {}, clear=False):
        statuses = apply_guest_mcp_servers(
            [server],
            starter=lambda spec: {"status": "error", "error": "FileNotFoundError: missing"},
        )

    # Then: the failure is reported with the server name and reason
    assert len(statuses) == 1
    assert statuses[0].kind == MCP_UNAVAILABLE
    assert statuses[0].subject == "broken"
    assert statuses[0].reason == "start_failed"


def test_capability_statuses_are_written_to_the_host_trace(tmp_path: Path) -> None:
    # Given: one missing-capability status
    status = CapabilityStatus(
        kind=MCP_UNAVAILABLE,
        subject="tavily",
        reason=REASON_CREDENTIAL_MISSING,
        detail="TAVILY_API_KEY, FACTORY_TAVILY_API_KEY",
    )

    # When: the host emits it into the trace workdir
    written = emit_capability_statuses(str(tmp_path), [status])

    # Then: the JSONL record is exactly the taxonomy-shaped event
    assert written == 1
    lines = (tmp_path / "cc_backend_trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == MCP_UNAVAILABLE
    assert record["subject"] == "tavily"
    assert record["reason"] == REASON_CREDENTIAL_MISSING
    assert isinstance(record["ts"], int)


# ── payload contract (todo 30 reuse) ─────────────────────────────────────────


def test_wire_payload_carries_the_extension_sections_and_the_guest_rehydrates_them() -> None:
    # Given: one placement and one asset built by the host
    server = McpServerPlacement(
        name="fake",
        program="python3",
        args=("/tmp/fake/server.py",),
        env=(("MODE", "test"),),
    )
    asset = SkillAsset(
        skill_id="demo-skill",
        path="references/notes.md",
        content=_encoded(b"ASSET-CONTENT-42"),
    )

    # When: they cross the versioned wire payload and the guest rehydrates
    wire = build_wire_task_payload(
        _host_input(mcp_servers=(server,), skill_assets=(asset,))
    )
    task = _round_trip(wire)

    # Then: the typed sections survive exactly
    assert wire["mcp_servers"] == [
        {
            "name": "fake",
            "program": "python3",
            "args": ["/tmp/fake/server.py"],
            "env": {"MODE": "test"},
            "placement": MCP_PLACEMENT_IN_GUEST,
            "credential_source": "none",
        }
    ]
    assert task.mcp_servers == (server,)
    assert task.skill_assets == (asset,)
    assert task.payload_version == wire["payload_version"]


def test_wire_payload_without_extensions_stays_byte_stable() -> None:
    # Given/When: a payload with no MCP servers and no assets (off-mode shape)
    wire = build_wire_task_payload(_host_input())

    # Then: the extension keys are absent - the todo-30 schema pin holds
    assert "mcp_servers" not in wire
    assert "skill_assets" not in wire


def test_malformed_extension_section_is_refused_not_defaulted() -> None:
    # Given: a payload whose mcp_servers entry is missing its program
    wire = build_wire_task_payload(_host_input())
    wire["mcp_servers"] = [{"name": "fake"}]

    # When/Then: the guest refuses instead of guessing an empty placement
    with pytest.raises(GuestTaskError) as refusal:
        _round_trip(wire)
    assert refusal.value.reason.startswith("payload_mcp_servers_invalid")

    # And: a non-list section is malformed too
    wire["mcp_servers"] = "fake"
    with pytest.raises(GuestTaskError) as refusal:
        _round_trip(wire)
    assert refusal.value.reason.startswith("payload_mcp_servers_invalid")


# ── manager wiring ───────────────────────────────────────────────────────────


class _FakeEndpoint:
    """Fake host half of one guest connection (PeerSession stand-in)."""

    def __init__(self, messages: list[ChannelMessage] | None = None) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self.closed = False

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class _FakeRegistry:
    def __init__(self, endpoint: _FakeEndpoint) -> None:
        self.endpoint = endpoint

    def session_for(self, session_id: SessionId) -> _FakeEndpoint | None:
        return self.endpoint

    def close(self) -> None:
        return None


def _stub_vm(workspace: Path) -> SessionVm:
    return SessionVm(
        session_id="sess-1",
        spec=DomainSpec(
            session_id="sess-1",
            base_image=workspace / "base.qcow2",
            workspace_host_path=workspace,
            overlay_dir=workspace / "overlays",
        ),
        domain=SessionDomain(SimpleNamespace()),
        export={"host_path": str(workspace), "guest_path": GUEST_WORKSPACE_PATH},
        peer_cid=PeerCid(11),
    )


def test_manager_dispatch_carries_extensions_rewrites_paths_and_emits_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a dispatch whose host runtime dir holds a materialized skill
    host_artifacts = tmp_path / "runtime"
    runtime = _write_skill(host_artifacts)
    server = McpServerPlacement(
        name="fake", program="python3", args=("/tmp/fake/server.py",)
    )
    asset = SkillAsset(
        skill_id="demo-skill",
        path="references/notes.md",
        content=_encoded((runtime / "references" / "notes.md").read_bytes()),
    )
    missing = CapabilityStatus(
        kind=MCP_UNAVAILABLE, subject="tavily", reason=REASON_CREDENTIAL_MISSING
    )
    collected = TaskExtensions(
        mcp_servers=(server,), skill_assets=(asset,), statuses=(missing,)
    )
    endpoint = _FakeEndpoint(
        [
            ChannelMessage(
                kind=MessageKind.RESULT,
                seq=1,
                payload={
                    "status": "ok",
                    "output_text": "done",
                    "usage": {},
                    "tool_calls_count": 0,
                },
            )
        ]
    )
    config = IsolationConfig.from_env({}, data_root=tmp_path)
    manager = IsolationManager(
        config,
        seams=ManagerSeams(
            channel=_FakeRegistry(endpoint),
            extensions=lambda payload: collected,
        ),
    )
    vm = _stub_vm(tmp_path / "workspace")
    monkeypatch.setattr(manager, "ensure_vm", lambda spec, **_kwargs: vm)
    payload = {
        "task_id": "task-1",
        "profile": "cc-test",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": "system",
        "user_message": "user",
        "skills_text": f"runtime_dir: {host_artifacts}/{SKILL_RUNTIME_SUBDIR}/demo-skill\n",
        "foundation_context": "foundation",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 4000,
        "timeout_s": 1200,
        "artifact_workdir": str(host_artifacts),
        "current_session": "sess-1",
    }

    # When: the manager dispatches the turn
    result = manager.run_task({"task_id": "task-1"}, str(tmp_path / "workspace"), payload)

    # Then: the guest frame carries both sections and a guest-valid runtime path
    assert result["status"] == "ok"
    assert len(endpoint.sent) == 1
    kind, wire = endpoint.sent[0]
    assert kind is MessageKind.TASK
    assert wire["mcp_servers"][0]["name"] == "fake"
    assert wire["skill_assets"][0]["path"] == "references/notes.md"
    guest_artifact = guest_artifact_dir(GUEST_WORKSPACE_PATH, "task-1")
    guest_root = guest_skills_root(guest_artifact)
    assert wire["artifact_workdir"] == guest_artifact
    assert wire["skills_text"] == f"runtime_dir: {guest_root}/demo-skill\n"
    assert str(host_artifacts) not in json.dumps(wire, ensure_ascii=False)
    # And: the missing capability is visible on the host trace, not silent
    records = [
        json.loads(line)
        for line in (host_artifacts / "cc_backend_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["kind"] for record in records] == [MCP_UNAVAILABLE]


def test_guest_roots_and_artifact_dir_constants_stay_aligned() -> None:
    # Given/When/Then: the guest_task default mirrors the manager's legacy
    # constant, and dispatch routes the artifact dir under the exported workspace
    assert DEFAULT_GUEST_ARTIFACT_DIR == GUEST_ARTIFACT_DIR
    assert guest_skills_root(DEFAULT_GUEST_ARTIFACT_DIR).startswith("/tmp/")
    assert guest_artifact_dir(GUEST_WORKSPACE_PATH, "task-1").startswith(
        f"{GUEST_WORKSPACE_PATH}/.igorek-artifacts/"
    )


def test_isolation_status_kinds_are_registered_in_the_taxonomy() -> None:
    # Given/When/Then: the visible statuses use the shared cc-backend taxonomy
    assert CcTraceEvent.ISOLATION_MCP_UNAVAILABLE == MCP_UNAVAILABLE
    assert CcTraceEvent.ISOLATION_MCP_BROKERED_PENDING == MCP_BROKERED_PENDING
    assert CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE == SKILL_ASSET_UNAVAILABLE


# ── guest runner integration (fake channel + fake MCP server) ────────────────


@dataclass
class _GuestHarness:
    host: PeerSession
    thread: threading.Thread
    errors: list[BaseException]
    exit_codes: list[int]

    def collect(self, timeout_s: float = 10.0) -> list[ChannelMessage]:
        messages: list[ChannelMessage] = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            message = self.host.recv_from_guest(
                timeout_s=max(0.05, deadline - time.monotonic())
            )
            if message is None:
                return messages
            messages.append(message)
            if message.kind in (MessageKind.RESULT, MessageKind.ERROR):
                return messages
        return messages

    def finish(self) -> None:
        self.host.close()
        self.thread.join(timeout=5.0)
        assert not self.thread.is_alive()
        assert self.errors == []


def _start_harness(executor: Any) -> _GuestHarness:
    config = ChannelConfig(max_frame_bytes=256 * 1024)
    cid = PeerCid(11)
    sessions = SessionMap()
    sessions.assign(cid, SessionId("sess-guest"))
    server = ChannelServer(config, sessions)
    srv_sock, cli_sock = socket.socketpair()
    errors: list[BaseException] = []
    exit_codes: list[int] = []

    def guest() -> None:
        try:
            session = guest_connect(cli_sock, config)
            runner = GuestRunner(
                session,
                seams=RunnerSeams(executor=executor, apply_egress=_fake_apply_egress),
                tuning=RunnerTuning(guest_roots=config.guest_roots, poll_interval_s=0.01),
            )
            try:
                exit_codes.append(runner.serve_one())
            finally:
                session.close()
        except BaseException as exc:  # reported to the test
            errors.append(exc)

    thread = threading.Thread(target=guest, daemon=True)
    thread.start()
    host = server.handle_connection(srv_sock, cid)
    assert host is not None
    return _GuestHarness(host=host, thread=thread, errors=errors, exit_codes=exit_codes)


def _guest_payload(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    return build_wire_task_payload(
        _host_input(
            workdir=str(tmp_path),
            artifact_workdir=str(tmp_path / "artifacts"),
            **overrides,
        )
    )


def _ok_executor(**kwargs: Any) -> dict[str, Any]:
    return {"status": "ok", "output_text": "done", "usage": {}, "tool_calls_count": 0}


def test_guest_runner_materializes_skill_assets_before_the_loop(tmp_path: Path) -> None:
    # Given: a task carrying one skill asset
    asset = SkillAsset(
        skill_id="demo-skill",
        path="references/notes.md",
        content=_encoded(b"ASSET-CONTENT-42"),
    )
    observed: dict[str, Any] = {}

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed["content"] = (
            tmp_path / "artifacts" / GUEST_SKILLS_SUBDIR / "demo-skill" / "references" / "notes.md"
        ).read_bytes()
        return _ok_executor(**kwargs)

    harness = _start_harness(executor)
    try:
        with patch.dict(os.environ, {}, clear=False):
            # When: the host dispatches the task
            harness.host.send(
                MessageKind.TASK, _guest_payload(tmp_path, skill_assets=(asset,))
            )
            messages = harness.collect()
    finally:
        harness.finish()

    # Then: the executor saw the exact bytes and the run finished cleanly
    assert observed["content"] == b"ASSET-CONTENT-42"
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    assert harness.exit_codes == [0]


def test_guest_runner_reports_a_brokered_server_as_pending(tmp_path: Path) -> None:
    # Given: a host-brokered spec in the task payload
    server = McpServerPlacement(
        name="secure",
        program="python3",
        args=("/tmp/secure/server.py",),
        placement=MCP_PLACEMENT_HOST_BROKERED,
        credential_source=MCP_CREDENTIAL_HOST_BROKER,
    )
    harness = _start_harness(_ok_executor)
    try:
        with patch.dict(os.environ, {}, clear=False):
            # When: the host dispatches the task
            harness.host.send(
                MessageKind.TASK, _guest_payload(tmp_path, mcp_servers=(server,))
            )
            messages = harness.collect()
    finally:
        harness.finish()

    # Then: an event frame names the pending broker capability before the result
    assert [message.kind for message in messages] == [
        MessageKind.EVENT,
        MessageKind.RESULT,
    ]
    record = dict(messages[0].payload["record"])
    assert record["kind"] == MCP_BROKERED_PENDING
    assert record["subject"] == "secure"


def test_guest_runner_reports_a_server_that_cannot_start(tmp_path: Path) -> None:
    # Given: an in-guest server whose program does not exist
    server = McpServerPlacement(
        name="broken",
        program=str(tmp_path / "does-not-exist"),
        args=(),
    )
    harness = _start_harness(_ok_executor)
    try:
        with patch.dict(os.environ, {}, clear=False):
            # When: the host dispatches the task
            harness.host.send(
                MessageKind.TASK, _guest_payload(tmp_path, mcp_servers=(server,))
            )
            messages = harness.collect()
    finally:
        harness.finish()

    # Then: the capability loss is visible, and the task still returns a result
    kinds = [message.kind for message in messages]
    assert kinds == [MessageKind.EVENT, MessageKind.RESULT]
    record = dict(messages[0].payload["record"])
    assert record["kind"] == MCP_UNAVAILABLE
    assert record["subject"] == "broken"
    assert record["reason"] == "start_failed"


def test_guest_runner_tools_list_succeeds_for_a_real_fake_server(tmp_path: Path) -> None:
    # Given: a real stdio MCP server script and its host-resolved placement
    marker = tmp_path / "tools_list_marker.txt"
    script = _fake_server_script(tmp_path)
    server = McpServerPlacement(
        name="fake",
        program="python3",
        args=(str(script), str(marker)),
    )
    observed: dict[str, Any] = {}

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed["has_tool"] = registry.has_tool(FAKE_TOOL)
        observed["call"] = registry.dispatch(FAKE_TOOL, {"text": "hi"})
        return _ok_executor(**kwargs)

    harness = _start_harness(executor)
    try:
        with patch.dict(os.environ, {}, clear=False):
            # When: the host dispatches the task
            harness.host.send(MessageKind.TASK, _guest_payload(tmp_path, mcp_servers=(server,)))
            messages = harness.collect()
    finally:
        harness.finish()
        registry.deregister(FAKE_TOOL)
        from factory.core.mcp_bridge import shutdown_mcp_servers

        shutdown_mcp_servers()

    # Then: the handshake ran in the guest (tools/list marker) and the tool is
    # registered and dispatchable inside the guest loop
    assert marker.read_text(encoding="utf-8") == "tools/list"
    assert observed["has_tool"] is True
    assert observed["call"] == "echo:hi"
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    assert harness.exit_codes == [0]
