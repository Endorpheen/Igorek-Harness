#!/usr/bin/env python3
"""TDD suite for the self-update split (plan todo 21, Wave 3).

Two update paths exist and must never be confused:

* inside a session VM the agent may run ``uv tool install --force --reinstall .``
  and that command may write only the GUEST environment's uv roots (the guest
  user's ``~/.cache/uv``, ``~/.local/share/uv/tools`` and ``~/.local/bin`` inside
  the disposable qcow2 overlay);
* the HOST runtime is updated only through the typed ``runtime.update`` broker
  op (todo 19/20): a host-owned policy decision, an explicit approval and an
  audit entry precede a fixed host-side argv. No guest shell, argv or path
  reaches it.

This suite pins the split structurally, not by wording:

* the guest's reinstall write set is confined to the guest's own uv roots and is
  disjoint from the host's uv roots (``build_self_update_split``);
* a guest environment that carries the host identity or a host uv root is
  refused before any command runs (``assert_guest_reinstall_confined``);
* no host uv root can ever be exported to the guest: the production workspace
  resolver refuses such a workspace and an extra export cannot smuggle one in
  (``assert_export_excludes_host_roots`` is wired into ``resolve_workspace_export``);
* the only reachable host update is the typed broker op: the catalog spec takes
  no arguments, the default policy requires an approval, the action runs the
  fixed host-side script exactly once after that approval, and raw shell/exec
  frames are denied with no host action;
* the in-guest sandbox binds only the guest uv roots, and a simulated guest
  shell write to a host uv root is denied by the existing scope guard and
  recorded in its audit trail.

Adversarial classes covered here:

* misleading success - every assertion is on the write sets, the exact argv, the
  audit trail and the refusal codes; no test asserts that "the split is done";
* fail-closed - a host write attempt is denied before any action, and a missing
  approval never runs the host update;
* dirty worktree - every test writes only into pytest temp dirs (the scope-guard
  audit trail is redirected there explicitly);
* regression - the guard behavior inside the guest is untouched: the guest
  reinstall is still ALLOWED, and only paths outside the guest roots are denied.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from factory.core import bash_scope_guard as guard
from factory.core.isolation import self_update as self_update
from factory.core.isolation.broker import (
    REASON_INVALID_ARGUMENTS,
    REASON_UNKNOWN_OPERATION,
    HostOpBroker,
    HostOpBrokerAuthority,
    HostOpCapabilityGrant,
    HostOpStatus,
)
from factory.core.isolation.broker_ops import BROKER_VERSION, default_host_op_registry
from factory.core.isolation.broker_seam import (
    HOST_OP_REGISTRY,
    HostOpPolicy,
    evaluate_host_op_request,
)
from factory.core.isolation.channel import ChannelMessage, MessageKind
from factory.core.isolation.errors import IsolationError
from factory.core.isolation.host_actions import CommandOutcome, HostActionLayer
from factory.core.isolation.host_escape import is_within
from factory.core.isolation.workspace import (
    ExtraExport,
    WorkspacePolicy,
    resolve_workspace_export,
)


def _guest_env(home: Path, **values: str) -> dict[str, str]:
    """A realistic in-guest process environment (its own HOME, guest marker)."""
    env = {
        "HOME": str(home),
        "IGOREK_ISOLATION_GUEST": "1",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    env.update(values)
    return env


def _host_env(home: Path, **values: str) -> dict[str, str]:
    """The host runtime environment this session is split away from."""
    env = {"HOME": str(home), "PATH": "/usr/local/bin:/usr/bin:/bin"}
    env.update(values)
    return env


@contextlib.contextmanager
def _ambient(env: Mapping[str, str]) -> Iterator[None]:
    """Run a section with exactly ``env`` as the process environment."""
    with mock.patch.dict(os.environ, dict(env), clear=True):
        yield


def _expected_roots(home: Path) -> tuple[str, ...]:
    return (
        str(home / ".cache" / "uv"),
        str(home / ".local" / "share" / "uv" / "tools"),
        str(home / ".local" / "bin"),
    )


def _overlaps(left: str, right: str) -> bool:
    return is_within(left, right) or is_within(right, left)


class _RecordingRunner:
    """Fake host command runner: records argv, never spawns anything."""

    def __init__(self, outcome: CommandOutcome | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], float, Path | None]] = []
        self.outcome = CommandOutcome(returncode=0) if outcome is None else outcome

    def run(
        self, argv: Sequence[str], *, timeout_s: float, cwd: Path | None = None
    ) -> CommandOutcome:
        self.calls.append((tuple(str(part) for part in argv), timeout_s, cwd))
        return self.outcome


class _RecordingTrail:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: Mapping[str, Any]) -> None:
        self.entries.append({str(key): value for key, value in entry.items()})


class _NoVault:
    """Vault must not be reached in this suite; any call is a bug."""

    def ephemeral_env(self, service: str, ttl_s: int) -> Mapping[str, str]:
        raise AssertionError("vault must not be reached by the update op")


class _NoTarget:
    def deliver(self, injection: Any) -> None:
        raise AssertionError("vault must not be reached by the update op")


def _layer(
    *,
    script: Path | None = None,
    outcome: CommandOutcome | None = None,
) -> tuple[HostActionLayer, _RecordingRunner]:
    runner = _RecordingRunner(outcome)
    return (
        HostActionLayer(
            runner=runner,
            vault=_NoVault(),
            vault_target=_NoTarget(),
            session_id="sess-self-update",
            update_script=script,
        ),
        runner,
    )


def _broker(
    layer: HostActionLayer, *, trail: _RecordingTrail | None = None
) -> HostOpBroker:
    return HostOpBroker(
        HostOpCapabilityGrant(registry=default_host_op_registry(), actions=layer.actions()),
        HostOpBrokerAuthority(
            policy=HostOpPolicy(allowed_ops=frozenset({"runtime.update"})),
            trail=_RecordingTrail() if trail is None else trail,
            session_id="sess-self-update",
        ),
        host_uid=1000,
    )


def _request(op: str, args: Mapping[str, Any] | None = None, *, seq: int = 11) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.HOST_OP,
        seq=seq,
        payload={"version": BROKER_VERSION, "op": op, "args": {} if args is None else dict(args)},
    )


def _workspace_policy(tmp_path: Path, **extra: Any) -> WorkspacePolicy:
    return WorkspacePolicy(
        home=tmp_path / "home",
        data_root=tmp_path / "data",
        host_uid=os.getuid(),
        **extra,
    )


# --- the guest environment update: confined to the guest's own uv roots -------


def test_guest_reinstall_writes_only_the_guest_uv_roots(tmp_path: Path) -> None:
    # Given: a guest session with its own home and a separate host runtime home
    guest_home = tmp_path / "home" / "agent"
    host_home = tmp_path / "home" / "example"
    guest_env = _guest_env(guest_home)
    host_env = _host_env(host_home)

    # When: the split for the in-guest `uv tool install --force --reinstall .` is built
    split = self_update.build_self_update_split(guest_env=guest_env, host_env=host_env)

    # Then: the reinstall is confined to the guest's own uv roots
    assert split.confined is True
    assert split.reason == ""
    assert split.guest_home == str(guest_home)
    assert split.guest_roots == _expected_roots(guest_home)

    # Then: every guest root lives under the guest home and none overlaps a host root
    assert split.host_roots == tuple(
        os.path.realpath(root) for root in _expected_roots(host_home)
    )
    for root in split.guest_roots:
        assert is_within(root, split.guest_home), root
        assert all(not _overlaps(root, host_root) for host_root in split.host_roots), root

    # Then: the documented command is the one that is modeled
    assert self_update.GUEST_REINSTALL_ARGV == (
        "uv",
        "tool",
        "install",
        "--force",
        "--reinstall",
        ".",
    )


def test_assert_guest_reinstall_confined_returns_the_checked_split(tmp_path: Path) -> None:
    # Given: a properly split guest and host environment
    guest_home = tmp_path / "home" / "agent"

    # When: the split is asserted
    split = self_update.assert_guest_reinstall_confined(
        guest_env=_guest_env(guest_home),
        host_env=_host_env(tmp_path / "home" / "example"),
    )

    # Then: the checked split is returned (no exception, confinement confirmed)
    assert split.confined is True
    assert split.guest_home == str(guest_home)


def test_guest_env_carrying_the_host_home_is_refused(tmp_path: Path) -> None:
    # Given: a guest process that inherited the HOST home (a leaked environment)
    host_home = tmp_path / "home" / "example"
    shared = {"HOME": str(host_home), "IGOREK_ISOLATION_GUEST": "1"}

    # When: the split is audited
    split = self_update.build_self_update_split(guest_env=shared, host_env=_host_env(host_home))

    # Then: it is refused with a stable code before any command could run
    assert split.confined is False
    assert split.reason == self_update.REASON_GUEST_HOME_IS_HOST_HOME
    with pytest.raises(IsolationError) as excinfo:
        self_update.assert_guest_reinstall_confined(
            guest_env=shared, host_env=_host_env(host_home)
        )
    assert excinfo.value.code == self_update.REASON_GUEST_HOME_IS_HOST_HOME


def test_guest_root_outside_the_guest_home_is_refused(tmp_path: Path) -> None:
    # Given: a guest uv override pointing outside the guest home
    guest_env = _guest_env(
        tmp_path / "home" / "agent", **{guard.TOOL_ROOTS_ENV: "/opt/igorek/uv"}
    )

    # When: the split is audited
    with pytest.raises(IsolationError) as excinfo:
        self_update.assert_guest_reinstall_confined(
            guest_env=guest_env, host_env=_host_env(tmp_path / "home" / "example")
        )

    # Then: the confinement failure names the offending root
    assert excinfo.value.code == self_update.REASON_GUEST_ROOT_OUTSIDE_HOME
    assert excinfo.value.reason == (
        f"{self_update.REASON_GUEST_ROOT_OUTSIDE_HOME}:/opt/igorek/uv"
    )


def test_guest_root_naming_a_host_uv_root_is_refused(tmp_path: Path) -> None:
    # Given: a guest env whose cache root is one of the HOST runtime's uv roots
    host_home = tmp_path / "home" / "example"
    host_cache = host_home / ".cache" / "uv"
    guest_env = _guest_env(tmp_path / "home" / "agent", UV_CACHE_DIR=str(host_cache))

    # When: the split is audited
    split = self_update.build_self_update_split(
        guest_env=guest_env, host_env=_host_env(host_home)
    )

    # Then: the overlap is refused with the offending root in the reason
    assert split.confined is False
    assert split.reason == f"{self_update.REASON_GUEST_ROOT_OVERLAPS_HOST}:{host_cache}"
    with pytest.raises(IsolationError) as excinfo:
        self_update.assert_guest_reinstall_confined(
            guest_env=guest_env, host_env=_host_env(host_home)
        )
    assert excinfo.value.code == self_update.REASON_GUEST_ROOT_OVERLAPS_HOST


def test_guest_env_without_a_home_is_refused(tmp_path: Path) -> None:
    # Given: a guest process with no HOME at all (uv would otherwise fall back to
    # the process user's home, which is exactly the confusion this guards against)
    guest_env = {"IGOREK_ISOLATION_GUEST": "1", "PATH": "/usr/bin:/bin"}

    # When/Then: the split is refused instead of guessed
    split = self_update.build_self_update_split(
        guest_env=guest_env, host_env=_host_env(tmp_path / "home" / "example")
    )
    assert split.confined is False
    assert split.reason == self_update.REASON_GUEST_HOME_MISSING


def test_host_env_without_a_home_is_refused(tmp_path: Path) -> None:
    # Given: a caller that cannot name the host home the split must be measured
    # against (no guess is made from the ambient process HOME)
    split = self_update.build_self_update_split(
        guest_env=_guest_env(tmp_path / "home" / "agent"),
        host_env={"PATH": "/usr/bin:/bin"},
    )

    # When/Then: fail closed instead of comparing against an unknown host
    assert split.confined is False
    assert split.reason == self_update.REASON_HOST_HOME_MISSING


# --- host uv roots are never exported to the guest ----------------------------


def test_resolved_workspace_export_contains_no_host_uv_root(tmp_path: Path) -> None:
    # Given: a normal project workspace under the host user's home
    home = tmp_path / "home"
    project = home / "repo" / "project"
    project.mkdir(parents=True)
    policy = _workspace_policy(tmp_path)

    # When: the session export is resolved by the production resolver
    with _ambient(_host_env(home)):
        export = resolve_workspace_export(project, policy=policy)

    # Then: the resolver itself proved the invariant (it calls the assertion)
    host_roots = self_update.host_uv_roots(home=home, env=_host_env(home))
    assert export["host_path"] == os.path.realpath(project)
    for root in host_roots:
        assert not _overlaps(os.path.realpath(export["host_path"]), root), root
    assert (
        self_update.assert_export_excludes_host_roots(export, home=home, env=_host_env(home))
        == host_roots
    )


def test_workspace_that_is_a_host_uv_root_is_refused(tmp_path: Path) -> None:
    # Given: the host runtime's uv cache chosen as the session workspace
    home = tmp_path / "home"
    uv_cache = home / ".cache" / "uv"
    uv_cache.mkdir(parents=True)
    policy = _workspace_policy(tmp_path)

    # When: the resolver is asked to export it
    with _ambient(_host_env(home)):
        with pytest.raises(IsolationError) as excinfo:
            resolve_workspace_export(uv_cache, policy=policy)

    # Then: the export is refused with the stable code, before any domain exists
    assert excinfo.value.code == self_update.REASON_HOST_ROOT_EXPORTED
    assert os.path.realpath(str(uv_cache)) in excinfo.value.detail


def test_extra_export_that_is_a_host_uv_root_is_refused(tmp_path: Path) -> None:
    # Given: a valid workspace plus an opt-in extra that smuggles in ~/.local/bin
    home = tmp_path / "home"
    project = home / "repo" / "project"
    project.mkdir(parents=True)
    host_bin = home / ".local" / "bin"
    host_bin.mkdir(parents=True)
    policy = _workspace_policy(
        tmp_path, extra_exports=(ExtraExport(cwd=str(host_bin), guest_path="/extra-bin"),)
    )

    # When: the resolver builds the export set
    with _ambient(_host_env(home)):
        with pytest.raises(IsolationError) as excinfo:
            resolve_workspace_export(project, policy=policy)

    # Then: the extra is refused too - no host uv root may cross the boundary
    assert excinfo.value.code == self_update.REASON_HOST_ROOT_EXPORTED
    assert os.path.realpath(str(host_bin)) in excinfo.value.detail


def test_export_check_refuses_every_exported_host_uv_root(tmp_path: Path) -> None:
    # Given: a hand-built export set that names a host uv root as its workspace
    home = tmp_path / "home"
    host_roots = self_update.host_uv_roots(home=home, env=_host_env(home))
    export: dict[str, object] = {
        "host_path": host_roots[0],
        "guest_path": "/workspace",
        "extra_exports": [],
    }

    # When/Then: the structural assertion refuses it and names the path
    with pytest.raises(IsolationError) as excinfo:
        self_update.assert_export_excludes_host_roots(export, home=home, env=_host_env(home))
    assert excinfo.value.code == self_update.REASON_HOST_ROOT_EXPORTED
    assert host_roots[0] in excinfo.value.detail


# --- the host runtime update: the typed broker op only ------------------------


def test_host_update_is_the_registered_typed_op_with_no_arguments() -> None:
    # Given: the host-update operation the split names
    assert self_update.HOST_UPDATE_OP == "runtime.update"

    # Then: it is the registered catalog spec, and it takes no guest argument
    assert self_update.HOST_UPDATE_OP in HOST_OP_REGISTRY
    spec = default_host_op_registry().spec(self_update.HOST_UPDATE_OP)
    assert spec is not None
    assert spec.arguments == ()
    assert self_update.host_update_request() == {
        "version": BROKER_VERSION,
        "op": "runtime.update",
        "args": {},
    }


def test_host_update_requires_an_explicit_approval_and_is_audited() -> None:
    # Given: the default host policy built from host configuration only
    policy = HostOpPolicy.from_host_env({})

    # Then: the op is allowed but never without the approval bit
    assert policy.allows(self_update.HOST_UPDATE_OP) is True
    assert policy.approval_required is True
    pending = evaluate_host_op_request(self_update.HOST_UPDATE_OP, policy=policy)
    assert (pending.allowed, pending.requires_approval, pending.reason) == (
        False,
        True,
        "approval_required",
    )
    approved = evaluate_host_op_request(
        self_update.HOST_UPDATE_OP, policy=policy, approval_granted=True
    )
    assert (approved.allowed, approved.audited) == (True, True)


def test_host_update_runs_through_the_broker_only_after_approval(tmp_path: Path) -> None:
    # Given: the documented host-side safe update script and a recording layer
    script = tmp_path / "update_safe.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    layer, runner = _layer(script=script)
    trail = _RecordingTrail()
    broker = _broker(layer, trail=trail)

    # When: the guest asks for the host update without an approval
    pending = broker.handle(_request("runtime.update"), approval_granted=False)

    # Then: nothing ran, and the request parked as approval_required
    assert pending.status is HostOpStatus.APPROVAL_REQUIRED
    assert runner.calls == []

    # When: the approval is granted and the same typed op is handled again
    approved = broker.handle(_request("runtime.update"), approval_granted=True)

    # Then: exactly one fixed host-side argv ran - `bash <configured script>`
    assert approved.status is HostOpStatus.OK
    assert runner.calls == [(("bash", str(script)), layer.update_timeout_s, None)]
    assert dict(approved.result) == {"updated": True, "update_script": script.name}
    # Then: the decision and the outcome are both audited (never silent):
    # the unapproved attempt parked (decision), then approve decision + outcome
    assert [entry["phase"] for entry in trail.entries] == ["decision", "decision", "outcome"]
    assert trail.entries[0]["op"] == "runtime.update"
    assert [entry["status"] for entry in trail.entries] == [
        "approval_required",
        "ok",
        "ok",
    ]
    assert trail.entries[-1]["executed"] is True
    # Then: no host path crosses the wire (the guest learns only the script name)
    assert str(tmp_path) not in json.dumps(approved.to_payload())


def test_no_guest_request_can_express_a_raw_shell_host_update(tmp_path: Path) -> None:
    # Given: a broker for the update op and a recording runner
    script = tmp_path / "update_safe.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    layer, runner = _layer(script=script)
    trail = _RecordingTrail()
    broker = _broker(layer, trail=trail)

    # When: the guest tries raw shell/exec shapes or widens the update schema
    attempts = (
        _request("shell", {"cmd": "uv tool install --force --reinstall ."}),
        _request("exec", {"argv": ["bash", "-c", "uv tool install --force --reinstall ."]}),
        _request("systemctl", {"args": "restart igorek-gateway.service"}),
        _request("runtime.update", {"script": "/tmp/evil.sh"}),
        _request("runtime.update", {"command": "uv tool install --force --reinstall ."}),
    )
    responses = [broker.handle(attempt, approval_granted=True) for attempt in attempts]

    # Then: every attempt is denied by the typed catalog, and nothing runs
    assert [response.status for response in responses] == [HostOpStatus.DENIED] * 5
    assert [response.reason for response in responses] == [
        REASON_UNKNOWN_OPERATION,
        REASON_UNKNOWN_OPERATION,
        REASON_UNKNOWN_OPERATION,
        REASON_INVALID_ARGUMENTS,
        REASON_INVALID_ARGUMENTS,
    ]
    assert runner.calls == []
    assert [entry["status"] for entry in trail.entries] == ["denied"] * 5


def test_failed_host_update_is_failed_never_ok(tmp_path: Path) -> None:
    # Given: the configured update script exits non-zero on the host
    script = tmp_path / "update_safe.sh"
    script.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
    layer, _runner = _layer(script=script, outcome=CommandOutcome(returncode=7))
    broker = _broker(layer)

    # When: the approved update runs
    response = broker.handle(_request("runtime.update"), approval_granted=True)

    # Then: the guest is told `failed`, never a misleading success
    assert response.status is HostOpStatus.FAILED
    assert response.reason == "update_failed"
    assert dict(response.result) == {}


# --- the in-guest sandbox and shell confinement --------------------------------


def test_guest_sandbox_binds_only_the_guest_uv_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an in-guest bwrap resolution with a fake binary present
    monkeypatch.setattr(
        "factory.core.bash_scope_guard.shutil.which", lambda _name: "/usr/bin/bwrap"
    )
    guest_home = tmp_path / "home" / "agent"
    workdir = guest_home / "workspace"
    workdir.mkdir(parents=True)
    guest_env = _guest_env(guest_home)
    host_home = tmp_path / "home" / "example"

    # When: the guest reinstall is wrapped for the hardened in-guest sandbox
    argv = guard.wrap_with_sandbox_argv(
        "uv tool install --force --reinstall .",
        workdir=str(workdir),
        env=guest_env,
        guest_mode=True,
    )

    # Then: only the guest uv roots are bound writable; no host uv root appears
    assert argv is not None
    binds = [argv[index + 1] for index, token in enumerate(argv) if token == "--bind"]
    for root in self_update.guest_uv_roots(guest_env):
        assert root in binds, root
    for host_root in self_update.host_uv_roots(home=host_home, env=_host_env(host_home)):
        assert host_root not in binds, host_root
    # Then: the hardened environment is used (no inherited host variables)
    assert "--clearenv" in argv
    # Then: every writable bind stays under the guest home
    for bind in binds:
        if bind == "/tmp":
            continue
        assert is_within(os.path.realpath(bind), os.path.realpath(str(guest_home))), bind


def test_guest_shell_write_to_a_host_uv_root_is_denied_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a guest process (its own HOME) with the guard on and an audit trail
    # redirected into the test temp dir
    guest_home = tmp_path / "home" / "agent"
    workdir = guest_home / "workspace"
    workdir.mkdir(parents=True)
    guest_env = _guest_env(guest_home)
    telemetry = tmp_path / "telemetry" / "denials.jsonl"
    monkeypatch.setattr(guard, "_telemetry_path", lambda: str(telemetry))
    monkeypatch.setattr(os, "environ", dict(guest_env))
    host_bin = Path("/home/example/.local/bin")

    # When: the simulated guest tries to write into a HOST uv root
    verdict, text = guard.enforce_bash_scope(
        f"touch {host_bin}/igorek-factory",
        guard_mode="on",
        workdir=str(workdir),
        filesystem_mode="workspace",
    )

    # Then: the write is denied, and the denial is recorded in the audit trail
    assert verdict == guard.VERDICT_DENY
    assert "bash_write_outside_workdir" in text
    records = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["verdict"] == "deny"
    assert records[-1]["target"] == str(host_bin / "igorek-factory")

    # Then (regression): the guest's own reinstall stays allowed in the guest
    allowed, _ = guard.enforce_bash_scope(
        "uv tool install --force --reinstall .",
        guard_mode="on",
        workdir=str(workdir),
        filesystem_mode="workspace",
    )
    assert allowed == guard.VERDICT_ALLOW
