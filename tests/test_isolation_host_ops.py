"""TDD suite for the typed host operations behind the broker catalog (plan todo 20).

Todo 19 built the broker (validate -> policy -> audit -> execute); this todo
backs three catalog ops with real typed functions:

* ``runtime.restart`` - restarts the named host runtime units (the documented
  replacement for the old ``systemd-run --user`` reach);
* ``runtime.update`` - runs the host-side safe update script
  (``factory/scripts/update_safe.sh`` pattern) with a fixed argv;
* ``vault.fetch`` - materializes a named Vault service's ephemeral environment
  host-side and injects it into the host runtime; the guest gets metadata only.

These tests pin the properties that matter for a security boundary, not a happy
path alone:

* every op runs through exactly one typed function - the injected runner records
  the exact argv, and that argv is a fixed host-side list, never guest text;
* the guest cannot widen the target/units/script: target is a closed CHOICE and
  an unmapped target fails closed;
* an argument outside the schema is rejected *before* any host action runs, and
  a policy-forbidden op performs no host action at all;
* a failed host action is a FAILED response, never a misleading ``ok``;
* the secret value never crosses the channel: the wire response and the audit
  trail carry only the service name, env names and ttl;
* the typed failures carry stable machine reasons into the response and the
  audit trail, while a plain (untyped) exception still maps to
  ``host_action_failed`` (todo-19 regression).

All host actions are faked here: no test may restart a real unit or run the real
update script.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from factory.core.isolation.broker import (
    REASON_HOST_ACTION_FAILED,
    REASON_INVALID_ARGUMENTS,
    REASON_OPERATION_NOT_ALLOWED,
    REASON_UNKNOWN_OPERATION,
    HostOpBroker,
    HostOpBrokerAuthority,
    HostOpCapabilityGrant,
    HostOpStatus,
)
from factory.core.isolation.broker_ops import (
    BROKER_VERSION,
    HostActionError,
    HostOpRegistry,
    HostOpSpec,
    default_host_op_registry,
)
from factory.core.isolation.broker_seam import HOST_OP_REGISTRY, HostOpPolicy
from factory.core.isolation.channel import ChannelMessage, MessageKind
from factory.core.isolation.host_actions import (
    DEFAULT_RESTART_UNITS,
    ENV_UPDATE_SCRIPT,
    REASON_ACTION_TIMEOUT,
    REASON_COMMAND_UNAVAILABLE,
    REASON_RESTART_FAILED,
    REASON_RESTART_TARGET_UNMAPPED,
    REASON_UPDATE_FAILED,
    REASON_UPDATE_SCRIPT_MISSING,
    CommandOutcome,
    HostActionLayer,
    SubprocessHostRunner,
    default_host_action_layer,
    default_update_script,
)
from factory.core.isolation.host_vault import (
    DEFAULT_VAULT_TTL_S,
    REASON_VAULT_FETCH_DENIED,
    REASON_VAULT_UNAVAILABLE,
    HostVaultEnvProvider,
    VaultInjection,
)
from factory.core.trace_taxonomy import CcTraceEvent

ALL_OPS = sorted(HOST_OP_REGISTRY)


class RecordingRunner:
    """Fake command runner: records argv and returns a canned outcome."""

    def __init__(self, outcome: CommandOutcome | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], float, Path | None]] = []
        self.outcome = CommandOutcome(returncode=0) if outcome is None else outcome

    def run(
        self, argv: Sequence[str], *, timeout_s: float, cwd: Path | None = None
    ) -> CommandOutcome:
        self.calls.append((tuple(str(part) for part in argv), timeout_s, cwd))
        return self.outcome


class RecordingTrail:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: Mapping[str, Any]) -> None:
        self.entries.append({str(key): value for key, value in entry.items()})


class FakeVault:
    """Fake host-side Vault broker: records calls, returns a fake ephemeral env."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, int]] = []
        self.env = {"WALLY_ACCESS_TOKEN": "supersecret-value"} if env is None else dict(env)
        self.error = error

    def ephemeral_env(self, service: str, ttl_s: int) -> Mapping[str, str]:
        self.calls.append((service, ttl_s))
        if self.error is not None:
            raise self.error
        return dict(self.env)


class RecordingTarget:
    """Fake host-side injection sink: records every delivered injection."""

    def __init__(self) -> None:
        self.injections: list[VaultInjection] = []

    def deliver(self, injection: VaultInjection) -> None:
        self.injections.append(injection)


@dataclass
class _LayerKit:
    layer: HostActionLayer
    runner: RecordingRunner
    vault: FakeVault
    target: RecordingTarget


def _kit(
    *,
    outcome: CommandOutcome | None = None,
    vault: FakeVault | None = None,
    target: RecordingTarget | None = None,
    script: Path | None = None,
    session_id: str = "sess-a",
) -> _LayerKit:
    recorded_runner = RecordingRunner(outcome)
    recorded_vault = FakeVault() if vault is None else vault
    recording_target = RecordingTarget() if target is None else target
    return _LayerKit(
        layer=HostActionLayer(
            runner=recorded_runner,
            vault=recorded_vault,
            vault_target=recording_target,
            session_id=session_id,
            update_script=script,
        ),
        runner=recorded_runner,
        vault=recorded_vault,
        target=recording_target,
    )


def _broker(
    layer: HostActionLayer,
    *,
    allowed: Sequence[str],
    trail: RecordingTrail | None = None,
    approval_required: bool = True,
) -> HostOpBroker:
    return HostOpBroker(
        HostOpCapabilityGrant(registry=default_host_op_registry(), actions=layer.actions()),
        HostOpBrokerAuthority(
            policy=HostOpPolicy(
                allowed_ops=frozenset(allowed), approval_required=approval_required
            ),
            trail=RecordingTrail() if trail is None else trail,
            session_id="sess-a",
        ),
        host_uid=1000,
    )


def _request(op: str, args: Mapping[str, Any] | None = None, *, seq: int = 7) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.HOST_OP,
        seq=seq,
        payload={"version": BROKER_VERSION, "op": op, "args": {} if args is None else dict(args)},
    )


# --- runtime.restart: one typed function, fixed argv, audited -----------------


def test_runtime_restart_runs_the_typed_action_once_and_is_audited() -> None:
    # Given: a restart op backed by a recording runner and an audit trail
    kit = _kit()
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["runtime.restart"], trail=trail)

    # When: the guest requests the gateway restart with approval granted
    response = broker.handle(
        _request("runtime.restart", {"target": "gateway"}, seq=21), approval_granted=True
    )

    # Then: exactly one fixed host command ran and its typed result is the response
    assert kit.runner.calls == [
        (("systemctl", "--user", "restart", "igorek-gateway.service"), kit.layer.restart_timeout_s, None)
    ]
    assert response.status is HostOpStatus.OK
    assert dict(response.result) == {
        "target": "gateway",
        "units": ["igorek-gateway.service"],
        "restarted": True,
    }
    # Then: decision-then-outcome audit under the taxonomy kind
    assert [entry["phase"] for entry in trail.entries] == ["decision", "outcome"]
    assert trail.entries[0]["kind"] == CcTraceEvent.ISOLATION_HOST_OP
    assert trail.entries[0]["op"] == "runtime.restart"
    assert trail.entries[1]["executed"] is True
    assert trail.entries[1]["reason"] == "approved"
    # Then: no host path leaks onto the wire
    assert "/" not in json.dumps(response.to_payload())


def test_runtime_restart_target_maps_to_host_units_the_guest_cannot_change() -> None:
    # Given: the two documented host-side restart targets
    assert DEFAULT_RESTART_UNITS == {
        "gateway": ("igorek-gateway.service",),
        "runtime": ("igorek-gateway.service", "igorek-mobile.service"),
    }

    # When: the guest asks for the full runtime target
    kit = _kit()
    broker = _broker(kit.layer, allowed=["runtime.restart"])
    response = broker.handle(
        _request("runtime.restart", {"target": "runtime"}), approval_granted=True
    )

    # Then: the typed function restarts the host-configured units, nothing else
    assert response.status is HostOpStatus.OK
    assert kit.runner.calls == [
        (
            (
                "systemctl",
                "--user",
                "restart",
                "igorek-gateway.service",
                "igorek-mobile.service",
            ),
            kit.layer.restart_timeout_s,
            None,
        )
    ]


def test_runtime_restart_never_accepts_a_guest_supplied_unit_name() -> None:
    kit = _kit()
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["runtime.restart"], trail=trail)

    # A unit name (or a unit-shaped string) is outside the closed CHOICE schema
    for args in (
        {"target": "igorek-gateway.service"},
        {"target": "systemctl"},
        {"target": "gateway", "command": "systemctl restart igorek-gateway.service"},
    ):
        response = broker.handle(_request("runtime.restart", args), approval_granted=True)
        assert response.status is HostOpStatus.DENIED
        assert response.reason == REASON_INVALID_ARGUMENTS

    assert kit.runner.calls == []
    assert [entry["reason"] for entry in trail.entries] == [REASON_INVALID_ARGUMENTS] * 3


def test_runtime_restart_failure_is_failed_never_ok() -> None:
    # Given: the host command exits non-zero
    kit = _kit(outcome=CommandOutcome(returncode=3, stderr="unit not found"))
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["runtime.restart"], trail=trail)

    # When: the restart runs
    response = broker.handle(
        _request("runtime.restart", {"target": "gateway"}), approval_granted=True
    )

    # Then: the response is FAILED with the stable reason, and the trail records it
    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_RESTART_FAILED
    assert response.result == {}
    assert kit.runner.calls and len(kit.runner.calls) == 1
    assert trail.entries[-1]["status"] == "failed"
    assert trail.entries[-1]["reason"] == REASON_RESTART_FAILED
    assert trail.entries[-1]["executed"] is True


def test_unmapped_restart_target_fails_closed_without_running_anything() -> None:
    kit = _kit()
    layer = HostActionLayer(
        runner=kit.runner,
        vault=kit.vault,
        vault_target=kit.target,
        units={"gateway": ("igorek-gateway.service",)},
    )
    trail = RecordingTrail()
    broker = _broker(layer, allowed=["runtime.restart"], trail=trail)

    # A target valid for the module default but not granted by this host mapping
    response = broker.handle(
        _request("runtime.restart", {"target": "runtime"}), approval_granted=True
    )

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_RESTART_TARGET_UNMAPPED
    assert kit.runner.calls == []
    assert trail.entries[-1]["executed"] is False


# --- runtime.update: the documented host-side safe path -----------------------


def test_runtime_update_runs_the_configured_safe_script_with_a_fixed_argv() -> None:
    with tempfile.TemporaryDirectory(prefix="host-op-update-") as tmp:
        script = Path(tmp) / "update_safe.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        kit = _kit(script=script)
        trail = RecordingTrail()
        broker = _broker(kit.layer, allowed=["runtime.update"], trail=trail)

        response = broker.handle(_request("runtime.update"), approval_granted=True)

        # Then: the guest cannot add arguments - argv is bash + the host script only
        assert kit.runner.calls == [(("bash", str(script)), kit.layer.update_timeout_s, None)]
        assert response.status is HostOpStatus.OK
        assert dict(response.result) == {"updated": True, "update_script": "update_safe.sh"}
        assert trail.entries[-1]["op"] == "runtime.update"
        assert trail.entries[-1]["status"] == "ok"
        # The wire carries no host path
        assert "/" not in json.dumps(response.to_payload())


def test_runtime_update_without_a_configured_script_fails_closed() -> None:
    kit = _kit(script=None)
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["runtime.update"], trail=trail)

    response = broker.handle(_request("runtime.update"), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_UPDATE_SCRIPT_MISSING
    assert kit.runner.calls == []
    assert trail.entries[-1]["reason"] == REASON_UPDATE_SCRIPT_MISSING
    assert trail.entries[-1]["executed"] is False


def test_runtime_update_with_a_missing_script_file_fails_closed() -> None:
    kit = _kit(script=Path("/nonexistent/update_safe.sh"))
    broker = _broker(kit.layer, allowed=["runtime.update"])

    response = broker.handle(_request("runtime.update"), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_UPDATE_SCRIPT_MISSING
    assert kit.runner.calls == []


def test_runtime_update_failure_is_failed_never_ok() -> None:
    with tempfile.TemporaryDirectory(prefix="host-op-update-") as tmp:
        script = Path(tmp) / "update_safe.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        kit = _kit(outcome=CommandOutcome(returncode=1), script=script)
        broker = _broker(kit.layer, allowed=["runtime.update"])

        response = broker.handle(_request("runtime.update"), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_UPDATE_FAILED


def test_runtime_update_takes_no_guest_arguments() -> None:
    kit = _kit()
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["runtime.update"], trail=trail)

    response = broker.handle(
        _request("runtime.update", {"mode": "fast"}), approval_granted=True
    )

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_INVALID_ARGUMENTS
    assert response.detail == "unknown_argument:mode"
    assert kit.runner.calls == []
    assert trail.entries[0]["reason"] == REASON_INVALID_ARGUMENTS


# --- vault.fetch: host-side materialization, no secret on the wire ------------


def test_vault_fetch_materializes_host_side_and_never_sends_the_secret() -> None:
    kit = _kit()
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["vault.fetch"], trail=trail)

    response = broker.handle(
        _request("vault.fetch", {"service": "wally"}, seq=33), approval_granted=True
    )

    # Then: the host-side Vault broker was called with the schema-typed args
    assert kit.vault.calls == [("wally", DEFAULT_VAULT_TTL_S)]
    # Then: the ephemeral env was injected host-side for this session
    assert kit.target.injections == [
        VaultInjection(
            session_id="sess-a",
            service="wally",
            env={"WALLY_ACCESS_TOKEN": "supersecret-value"},
            ttl_s=DEFAULT_VAULT_TTL_S,
        )
    ]
    # Then: the guest gets metadata only - the value never crosses or is audited
    assert response.status is HostOpStatus.OK
    assert dict(response.result) == {
        "service": "wally",
        "ttl_s": DEFAULT_VAULT_TTL_S,
        "env_names": ["WALLY_ACCESS_TOKEN"],
        "injected": True,
    }
    rendered = json.dumps(response.to_payload()) + json.dumps(trail.entries)
    assert "supersecret-value" not in rendered


def test_vault_fetch_passes_the_bounded_ttl_through() -> None:
    kit = _kit()
    broker = _broker(kit.layer, allowed=["vault.fetch"])

    response = broker.handle(
        _request("vault.fetch", {"service": "github", "ttl_s": 120}), approval_granted=True
    )

    assert response.status is HostOpStatus.OK
    assert kit.vault.calls == [("github", 120)]
    assert kit.target.injections[0].ttl_s == 120


def test_vault_fetch_denied_by_vault_policy_is_audited_without_injection() -> None:
    denied = HostActionError(REASON_VAULT_FETCH_DENIED, "service not allowed", executed=False)
    kit = _kit(vault=FakeVault(error=denied))
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["vault.fetch"], trail=trail)

    response = broker.handle(
        _request("vault.fetch", {"service": "wally"}), approval_granted=True
    )

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_VAULT_FETCH_DENIED
    assert kit.target.injections == []
    assert trail.entries[-1]["reason"] == REASON_VAULT_FETCH_DENIED
    assert trail.entries[-1]["executed"] is False


def test_vault_fetch_provider_failure_is_a_stable_reason() -> None:
    unavailable = HostActionError(REASON_VAULT_UNAVAILABLE, "vault_locked", executed=False)
    kit = _kit(vault=FakeVault(error=unavailable))
    broker = _broker(kit.layer, allowed=["vault.fetch"])

    response = broker.handle(
        _request("vault.fetch", {"service": "wally"}), approval_granted=True
    )

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_VAULT_UNAVAILABLE
    assert kit.target.injections == []


def test_vault_fetch_arguments_outside_the_schema_never_reach_the_vault() -> None:
    kit = _kit()
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["vault.fetch"], trail=trail)

    for args in (
        {},
        {"service": "/etc/passwd"},
        {"service": "wally", "ttl_s": 5},
        {"service": "wally", "ttl_s": True},
        {"service": "wally", "path": "~/.ssh/id_rsa"},
    ):
        response = broker.handle(_request("vault.fetch", args), approval_granted=True)
        assert response.status is HostOpStatus.DENIED
        assert response.reason == REASON_INVALID_ARGUMENTS

    assert kit.vault.calls == []
    assert kit.target.injections == []
    assert [entry["reason"] for entry in trail.entries] == [REASON_INVALID_ARGUMENTS] * 5


# --- policy, approval gate, catalog coverage ----------------------------------


def test_policy_forbidden_operations_perform_no_host_action() -> None:
    kit = _kit(script=Path("/nonexistent/update_safe.sh"))
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=["runtime.restart"], trail=trail)

    update = broker.handle(_request("runtime.update"), approval_granted=True)
    fetch = broker.handle(_request("vault.fetch", {"service": "wally"}), approval_granted=True)

    assert update.status is HostOpStatus.DENIED
    assert update.reason == REASON_OPERATION_NOT_ALLOWED
    assert fetch.status is HostOpStatus.DENIED
    assert fetch.reason == REASON_OPERATION_NOT_ALLOWED
    # The denied ops were audited once each, and nothing ran
    assert [entry["reason"] for entry in trail.entries] == [REASON_OPERATION_NOT_ALLOWED] * 2
    assert all(entry["allowed"] is False for entry in trail.entries)
    assert kit.runner.calls == []
    assert kit.vault.calls == []
    assert kit.target.injections == []


def test_host_operations_require_approval_before_they_run() -> None:
    kit = _kit()
    broker = _broker(kit.layer, allowed=ALL_OPS)

    restart = broker.handle(_request("runtime.restart", {"target": "gateway"}))
    update = broker.handle(_request("runtime.update"))
    fetch = broker.handle(_request("vault.fetch", {"service": "wally"}))

    for response in (restart, update, fetch):
        assert response.status is HostOpStatus.APPROVAL_REQUIRED
        assert response.reason == "approval_required"
    assert kit.runner.calls == []
    assert kit.vault.calls == []


def test_unknown_operations_never_reach_a_typed_function() -> None:
    kit = _kit()
    trail = RecordingTrail()
    broker = _broker(kit.layer, allowed=ALL_OPS, trail=trail)

    response = broker.handle(_request("runtime.exec", {"argv": ["systemctl", "restart"]}))

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_UNKNOWN_OPERATION
    assert kit.runner.calls == []
    assert trail.entries[0]["reason"] == REASON_UNKNOWN_OPERATION


def test_every_catalog_operation_has_exactly_one_typed_function() -> None:
    kit = _kit()

    actions = kit.layer.actions()

    assert sorted(actions) == ALL_OPS
    assert all(callable(action) for action in actions.values())
    # The grant itself refuses a catalog/action mismatch - construct it to prove
    # the production wiring covers every op with a host-owned function.
    grant = HostOpCapabilityGrant(registry=default_host_op_registry(), actions=actions)
    assert set(grant.actions) == set(default_host_op_registry().ops)


def test_runtime_status_reports_the_catalog_without_touching_the_host() -> None:
    kit = _kit()
    broker = _broker(kit.layer, allowed=["runtime.status"])

    response = broker.handle(_request("runtime.status"), approval_granted=True)

    assert response.status is HostOpStatus.OK
    assert dict(response.result) == {"broker_version": BROKER_VERSION, "ops": ALL_OPS}
    assert kit.runner.calls == []


# --- typed failures vs plain exceptions (todo-19 regression) ------------------


def _single_op_registry() -> HostOpRegistry:
    return HostOpRegistry((HostOpSpec("runtime.status"),))


def test_plain_action_exception_still_maps_to_host_action_failed() -> None:
    def exploding(_args: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("cannot write /home/example/.local/bin")

    trail = RecordingTrail()
    broker = HostOpBroker(
        HostOpCapabilityGrant(
            registry=_single_op_registry(), actions={"runtime.status": exploding}
        ),
        HostOpBrokerAuthority(
            policy=HostOpPolicy(allowed_ops=frozenset({"runtime.status"})),
            trail=trail,
            session_id="sess-a",
        ),
        host_uid=1000,
    )

    response = broker.handle(_request("runtime.status"), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_HOST_ACTION_FAILED
    assert response.detail == "RuntimeError"
    assert "home" not in json.dumps(response.to_payload())


# --- the real runner: argv list, no shell, typed failures ---------------------


def test_subprocess_runner_returns_the_exit_code_and_captured_output() -> None:
    runner = SubprocessHostRunner()

    ok = runner.run([sys.executable, "-c", "print('ok')"], timeout_s=30.0)
    bad = runner.run([sys.executable, "-c", "raise SystemExit(3)"], timeout_s=30.0)

    assert ok.returncode == 0
    assert ok.stdout.strip() == "ok"
    assert bad.returncode == 3


def test_subprocess_runner_reports_a_timeout_as_a_typed_error() -> None:
    runner = SubprocessHostRunner()

    with pytest.raises(HostActionError) as caught:
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(10)"], timeout_s=0.5
        )

    assert caught.value.code == REASON_ACTION_TIMEOUT


def test_subprocess_runner_reports_a_missing_binary_as_a_typed_error() -> None:
    runner = SubprocessHostRunner()

    with pytest.raises(HostActionError) as caught:
        runner.run(["/nonexistent/igorek-no-such-binary"], timeout_s=5.0)

    assert caught.value.code == REASON_COMMAND_UNAVAILABLE


# --- the production layer ------------------------------------------------------


def test_default_update_script_is_the_documented_safe_path() -> None:
    script = default_update_script()

    assert script.name == "update_safe.sh"
    assert script.parent.name == "scripts"


def test_default_layer_uses_the_host_configured_update_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrapper = tmp_path / "host_update_wrapper.sh"
    monkeypatch.setenv(ENV_UPDATE_SCRIPT, str(wrapper))

    layer = default_host_action_layer(session_id="sess-a")

    assert layer.update_script == wrapper
    assert isinstance(layer.runner, SubprocessHostRunner)
    assert isinstance(layer.vault, HostVaultEnvProvider)
    assert sorted(layer.actions()) == ALL_OPS
