"""TDD suite for the typed host-operation broker (plan todo 19).

The broker is the only way a guest can ask the host to do anything outside the
VM, so these tests pin structural guarantees rather than a happy path alone:

* operations are a finite host-owned catalog with typed argument schemas -
  there is deliberately no free-form string kind, so no op can ever carry a
  shell command, argv or host path;
* unknown ops and schema violations are denied with stable machine reasons
  (``unknown_operation`` / ``invalid_arguments``) and every request is audited;
* the policy is host-side: a guest-supplied policy cannot even construct a
  broker, and a policy smuggled inside a request payload is a malformed request;
* the audit trail is written *before* execution, so an unavailable audit sink
  blocks the action instead of letting it run unaudited;
* a harmless-looking "handled" response is never enough - every test asserts
  the action call list, the typed result and the audit entries.

Wire expectation: one request frame of the existing channel kind ``host_op``
(``{"version", "op", "args"}``) produces exactly one response frame of the same
kind, and the response itself passes the channel payload policy.
"""

from __future__ import annotations

import json
import socket
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from factory.core.isolation.broker import (
    BROKER_VERSION,
    REASON_AUDIT_UNAVAILABLE,
    REASON_HOST_ACTION_FAILED,
    REASON_INVALID_ACTION_RESULT,
    REASON_INVALID_ARGUMENTS,
    REASON_MALFORMED_REQUEST,
    REASON_OPERATION_NOT_ALLOWED,
    REASON_PRIVILEGED_USER,
    REASON_UNKNOWN_OPERATION,
    REASON_UNSUPPORTED_VERSION,
    HostOpBroker,
    HostOpBrokerAuthority,
    HostOpCapabilityGrant,
    HostOpStatus,
    JsonlHostOpAudit,
)
from factory.core.isolation.broker_ops import (
    ArgKind,
    HostOpArgument,
    HostOpArgumentError,
    HostOpRegistry,
    HostOpSpec,
    default_host_op_registry,
    validate_host_op_args,
)
from factory.core.isolation.broker_seam import (
    DEFAULT_ALLOWED_OPS,
    HOST_OP_REGISTRY,
    HOST_POLICY_SOURCE,
    HostOpPolicy,
)
from factory.core.isolation.channel import (
    ChannelConfig,
    ChannelMessage,
    MessageKind,
    PeerIdentity,
    PeerSession,
    payload_policy_violation,
)
from factory.core.isolation.errors import IsolationError
from factory.core.trace_taxonomy import CcTraceEvent

ALL_OPS = sorted(HOST_OP_REGISTRY)
READ_ONLY_OP = "runtime.status"


class RecordingTrail:
    """Fake audit sink: keeps every record for exact assertions."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: Mapping[str, Any]) -> None:
        self.entries.append({str(key): value for key, value in entry.items()})


class ExplodingTrail:
    """Audit sink that is down; execution must not continue."""

    def record(self, entry: Mapping[str, Any]) -> None:
        raise RuntimeError("audit backend down")


class FakeHostActions:
    """Fake host action layer: records calls and returns one typed result."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {"mode": "kvm"} if result is None else result

    def __call__(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(args))
        return dict(self.result)


def _registry(*ops: str) -> HostOpRegistry:
    return HostOpRegistry(tuple(HostOpSpec(op) for op in ops))


def _policy(*ops: str, approval_required: bool = True) -> HostOpPolicy:
    return HostOpPolicy(allowed_ops=frozenset(ops), approval_required=approval_required)


HostActionLike = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _broker(
    actions: Mapping[str, HostActionLike],
    *,
    policy: HostOpPolicy | None = None,
    trail: RecordingTrail | None = None,
    registry: HostOpRegistry | None = None,
) -> HostOpBroker:
    return HostOpBroker(
        HostOpCapabilityGrant(
            registry=_registry(READ_ONLY_OP) if registry is None else registry,
            actions=dict(actions),
        ),
        HostOpBrokerAuthority(
            policy=_policy(READ_ONLY_OP) if policy is None else policy,
            trail=RecordingTrail() if trail is None else trail,
            session_id="sess-a",
        ),
        host_uid=1000,
    )


def _full_actions(**overrides: HostActionLike) -> dict[str, HostActionLike]:
    actions: dict[str, HostActionLike] = {op: FakeHostActions({"op": op}) for op in ALL_OPS}
    actions.update(overrides)
    return actions


def _default_broker(trail: RecordingTrail, **overrides: HostActionLike) -> HostOpBroker:
    return _broker(
        _full_actions(**overrides),
        policy=_policy(*ALL_OPS),
        trail=trail,
        registry=default_host_op_registry(),
    )


def _request(
    op: object,
    args: Mapping[str, Any] | None = None,
    *,
    version: object = BROKER_VERSION,
    seq: int = 7,
) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.HOST_OP,
        seq=seq,
        payload={"version": version, "op": op, "args": {} if args is None else dict(args)},
    )


def _valid_value(argument: HostOpArgument) -> object:
    match argument.kind:
        case ArgKind.NAME:
            return "svc_name"
        case ArgKind.CHOICE:
            return sorted(argument.choices)[0]
        case ArgKind.INTEGER:
            return 60 if argument.minimum is None else argument.minimum
        case ArgKind.BOOLEAN:
            return True
        case _ as unreachable:  # pragma: no cover - exhaustive guard
            raise AssertionError(unreachable)


def _valid_args_for(spec: HostOpSpec) -> dict[str, Any]:
    return {argument.name: _valid_value(argument) for argument in spec.arguments}


# --- allowed operations: execute once, audited before and after --------------


def test_allowed_operation_executes_and_is_audited() -> None:
    # Given: an allowlisted read-only op backed by a recording host action
    action = FakeHostActions({"broker_version": BROKER_VERSION, "ops": [READ_ONLY_OP]})
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)

    # When: the guest requests it with approval already granted
    response = broker.handle(_request(READ_ONLY_OP, seq=11), approval_granted=True)

    # Then: the typed action ran exactly once and its result is the response
    assert action.calls == [{}]
    assert response.status is HostOpStatus.OK
    assert response.reason == "approved"
    assert dict(response.result) == action.result
    assert response.version == BROKER_VERSION
    assert response.request_seq == 11
    # Then: the decision was audited before the outcome, both under the taxonomy kind
    assert [entry["phase"] for entry in trail.entries] == ["decision", "outcome"]
    assert trail.entries[0]["kind"] == CcTraceEvent.ISOLATION_HOST_OP
    assert trail.entries[0]["allowed"] is True
    assert trail.entries[0]["reason"] == "approved"
    assert trail.entries[0]["session"] == "sess-a"
    assert trail.entries[1]["executed"] is True
    assert trail.entries[1]["status"] == "ok"


def test_approval_is_required_before_any_host_action() -> None:
    action = FakeHostActions()
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)

    pending = broker.handle(_request(READ_ONLY_OP), approval_granted=False)

    assert pending.status is HostOpStatus.APPROVAL_REQUIRED
    assert pending.reason == "approval_required"
    assert action.calls == []
    assert len(trail.entries) == 1
    assert trail.entries[0]["requires_approval"] is True
    assert trail.entries[0]["reason"] == "approval_required"

    granted = broker.handle(_request(READ_ONLY_OP), approval_granted=True)
    assert granted.status is HostOpStatus.OK
    assert len(action.calls) == 1


# --- denials: unknown op, ungranted op, malformed args -----------------------


def test_unknown_operation_is_denied_and_audited() -> None:
    action = FakeHostActions()
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)

    response = broker.handle(_request("shell", {"cmd": "rm -rf /"}), approval_granted=True)

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_UNKNOWN_OPERATION
    assert action.calls == []
    assert len(trail.entries) == 1
    assert trail.entries[0]["allowed"] is False
    assert trail.entries[0]["reason"] == REASON_UNKNOWN_OPERATION
    # Adversarial: the denied shell text is never persisted into the audit trail
    assert "rm -rf" not in json.dumps(trail.entries)


def test_known_operation_outside_the_allowlist_is_denied_and_audited() -> None:
    status_action = FakeHostActions()
    update_action = FakeHostActions()
    trail = RecordingTrail()
    broker = _broker(
        {READ_ONLY_OP: status_action, "runtime.update": update_action},
        policy=_policy(READ_ONLY_OP),
        trail=trail,
        registry=_registry(READ_ONLY_OP, "runtime.update"),
    )

    response = broker.handle(_request("runtime.update"), approval_granted=True)

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_OPERATION_NOT_ALLOWED
    assert update_action.calls == []
    assert trail.entries[0]["reason"] == REASON_OPERATION_NOT_ALLOWED


@pytest.mark.parametrize(
    ("args", "detail"),
    [
        ({}, "missing_argument:service"),
        ({"service": "/etc/passwd"}, "invalid_value:service"),
        ({"service": "~/.ssh"}, "invalid_value:service"),
        ({"service": "a b"}, "invalid_value:service"),
        ({"service": "svc", "cmd": "rm -rf /"}, "unknown_argument:cmd"),
        ({"service": "svc", "ttl_s": True}, "invalid_value:ttl_s"),
        ({"service": "svc", "ttl_s": "60"}, "invalid_value:ttl_s"),
        ({"service": "svc", "ttl_s": 99999}, "out_of_range:ttl_s"),
        ({"service": "svc", "ttl_s": 5}, "out_of_range:ttl_s"),
    ],
)
def test_malformed_arguments_are_denied_and_audited(args: dict[str, Any], detail: str) -> None:
    action = FakeHostActions()
    trail = RecordingTrail()
    broker = _default_broker(trail, **{"vault.fetch": action})

    response = broker.handle(_request("vault.fetch", args), approval_granted=True)

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_INVALID_ARGUMENTS
    assert response.detail == detail
    assert action.calls == []
    assert trail.entries[0]["reason"] == REASON_INVALID_ARGUMENTS
    assert trail.entries[0]["allowed"] is False


# --- capability containment: the guest can never widen the op set ------------


def test_guest_supplied_policy_is_rejected() -> None:
    guest_policy = HostOpPolicy(allowed_ops=frozenset({READ_ONLY_OP}), source="guest_payload")

    with pytest.raises(IsolationError) as caught:
        HostOpBrokerAuthority(policy=guest_policy, trail=RecordingTrail(), session_id="sess-a")

    assert caught.value.code == "guest_policy_rejected"


def test_guest_cannot_smuggle_a_policy_inside_the_request() -> None:
    action = FakeHostActions()
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)
    payload = {
        "version": BROKER_VERSION,
        "op": READ_ONLY_OP,
        "args": {},
        "policy": {"source": "guest_payload", "allowed_ops": ["shell"]},
        "allowed_ops": ["shell"],
    }

    response = broker.handle(
        ChannelMessage(kind=MessageKind.HOST_OP, seq=3, payload=payload), approval_granted=True
    )

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_MALFORMED_REQUEST
    assert action.calls == []
    assert trail.entries[0]["reason"] == REASON_MALFORMED_REQUEST
    assert "shell" not in json.dumps(response.to_payload())


def test_default_catalog_is_bounded_by_the_seam_registry() -> None:
    catalog = default_host_op_registry()

    assert catalog.ops == HOST_OP_REGISTRY
    assert DEFAULT_ALLOWED_OPS <= HOST_OP_REGISTRY
    assert HostOpPolicy.from_host_env({}).source == HOST_POLICY_SOURCE

    with pytest.raises(IsolationError) as caught:
        HostOpRegistry((HostOpSpec("shell"),))
    assert caught.value.code == "unregistered_operation"


def test_capability_grant_must_cover_every_registered_op_exactly() -> None:
    with pytest.raises(IsolationError) as missing:
        HostOpCapabilityGrant(registry=_registry(READ_ONLY_OP), actions={})
    assert missing.value.code == "incomplete_capability_grant"

    with pytest.raises(IsolationError) as extra:
        HostOpCapabilityGrant(
            registry=_registry(READ_ONLY_OP),
            actions={READ_ONLY_OP: FakeHostActions(), "shell": FakeHostActions()},
        )
    assert extra.value.code == "incomplete_capability_grant"


def test_registry_rejects_malformed_specifications() -> None:
    with pytest.raises(IsolationError) as duplicate:
        HostOpRegistry((HostOpSpec(READ_ONLY_OP), HostOpSpec(READ_ONLY_OP)))
    assert duplicate.value.code == "duplicate_operation"

    with pytest.raises(IsolationError) as malformed:
        HostOpRegistry((HostOpSpec("runtime status"),))
    assert malformed.value.code == "invalid_operation_name"

    with pytest.raises(IsolationError) as bad_argument:
        HostOpRegistry(
            (
                HostOpSpec(
                    READ_ONLY_OP,
                    (HostOpArgument("Bad Name", ArgKind.NAME),),
                ),
            )
        )
    assert bad_argument.value.code == "invalid_argument_name"

    with pytest.raises(IsolationError) as bad_choices:
        HostOpRegistry((HostOpSpec(READ_ONLY_OP, (HostOpArgument("target", ArgKind.CHOICE),)),))
    assert bad_choices.value.code == "invalid_argument_spec"


def test_broker_refuses_a_privileged_host_user() -> None:
    grant = HostOpCapabilityGrant(
        registry=_registry(READ_ONLY_OP), actions={READ_ONLY_OP: FakeHostActions()}
    )
    authority = HostOpBrokerAuthority(policy=_policy(READ_ONLY_OP), trail=RecordingTrail())

    with pytest.raises(IsolationError) as caught:
        HostOpBroker(grant, authority, host_uid=0)

    assert caught.value.code == REASON_PRIVILEGED_USER


# --- the op set itself: no op can ever carry shell text or a host path -------


def test_no_default_operation_accepts_shell_text_or_host_paths() -> None:
    catalog = default_host_op_registry()
    actions = {op: FakeHostActions({"op": op}) for op in ALL_OPS}
    broker = _broker(actions, policy=_policy(*ALL_OPS), registry=catalog)

    for spec in catalog.all_specs():
        for argument in spec.arguments:
            # There is no free-form text kind at all
            assert argument.kind in {ArgKind.NAME, ArgKind.CHOICE, ArgKind.INTEGER, ArgKind.BOOLEAN}

        # A shell-shaped argument name is not part of any schema
        for shell_key in ("cmd", "command", "shell", "argv", "exec", "script"):
            response = broker.handle(
                _request(spec.op, {**_valid_args_for(spec), shell_key: "rm -rf /"}),
                approval_granted=True,
            )
            assert response.status is HostOpStatus.DENIED, (spec.op, shell_key)
            assert response.reason == REASON_INVALID_ARGUMENTS, (spec.op, shell_key)

        # Host paths, home paths and shell metacharacters never validate
        for bad in ("/etc/passwd", "~/.ssh/id_rsa", "a b", "a;b", "a|b", "a$(id)", "..", "a/b", ""):
            for argument in spec.arguments:
                args = _valid_args_for(spec)
                args[argument.name] = bad
                response = broker.handle(_request(spec.op, args), approval_granted=True)
                assert response.status is HostOpStatus.DENIED, (spec.op, argument.name, bad)
                assert response.reason == REASON_INVALID_ARGUMENTS, (spec.op, argument.name, bad)

    assert all(not action.calls for action in actions.values())


def test_argument_validation_returns_only_typed_scalars() -> None:
    spec = HostOpSpec(
        "vault.fetch",
        (
            HostOpArgument("service", ArgKind.NAME, required=True),
            HostOpArgument("ttl_s", ArgKind.INTEGER, minimum=60, maximum=3600),
        ),
    )

    assert validate_host_op_args(spec, {"service": "wally", "ttl_s": 120}) == {
        "service": "wally",
        "ttl_s": 120,
    }
    with pytest.raises(HostOpArgumentError) as caught:
        validate_host_op_args(spec, {"service": "wally", "ttl_s": "120"})
    assert (caught.value.problem, caught.value.argument) == ("invalid_value", "ttl_s")


# --- versioning, audit failure, action failure, caller errors ----------------


def test_unsupported_broker_version_is_denied_and_audited() -> None:
    action = FakeHostActions()
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)
    missing_version = ChannelMessage(
        kind=MessageKind.HOST_OP, seq=4, payload={"op": READ_ONLY_OP, "args": {}}
    )

    for message in (_request(READ_ONLY_OP, version=BROKER_VERSION + 1), missing_version):
        response = broker.handle(message, approval_granted=True)
        assert response.status is HostOpStatus.DENIED
        assert response.reason == REASON_UNSUPPORTED_VERSION

    assert action.calls == []
    assert [entry["reason"] for entry in trail.entries] == [REASON_UNSUPPORTED_VERSION] * 2


def test_audit_failure_blocks_execution() -> None:
    action = FakeHostActions()
    broker = HostOpBroker(
        HostOpCapabilityGrant(
            registry=_registry(READ_ONLY_OP), actions={READ_ONLY_OP: action}
        ),
        HostOpBrokerAuthority(policy=_policy(READ_ONLY_OP), trail=ExplodingTrail()),
        host_uid=1000,
    )

    response = broker.handle(_request(READ_ONLY_OP), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_AUDIT_UNAVAILABLE
    assert action.calls == []


def test_host_action_failure_is_reported_without_leaking_host_detail() -> None:
    def exploding(_args: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("cannot write /home/example/.local/bin")

    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: exploding}, trail=trail)

    response = broker.handle(_request(READ_ONLY_OP), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_HOST_ACTION_FAILED
    assert response.detail == "RuntimeError"
    assert "home" not in json.dumps(response.to_payload())
    assert trail.entries[-1]["status"] == "failed"
    assert trail.entries[-1]["executed"] is True


def test_host_action_result_carrying_a_host_path_is_rejected() -> None:
    action = FakeHostActions({"path": "/home/example/.ssh/id_rsa"})
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)

    response = broker.handle(_request(READ_ONLY_OP), approval_granted=True)

    assert response.status is HostOpStatus.FAILED
    assert response.reason == REASON_INVALID_ACTION_RESULT
    assert "home" not in json.dumps(response.to_payload())
    assert trail.entries[-1]["status"] == "failed"


def test_non_host_op_frames_are_a_caller_error() -> None:
    broker = _broker({READ_ONLY_OP: FakeHostActions()})

    with pytest.raises(IsolationError) as caught:
        broker.handle(ChannelMessage(kind=MessageKind.RESULT, seq=1, payload={"status": "ok"}))

    assert caught.value.code == "unexpected_frame_kind"


def test_unvalidated_guest_text_never_reaches_response_or_audit() -> None:
    action = FakeHostActions()
    trail = RecordingTrail()
    broker = _broker({READ_ONLY_OP: action}, trail=trail)

    response = broker.handle(
        _request("shell;rm -rf /home/example", {"cmd": "/home/example/secret"}),
        approval_granted=True,
    )
    rendered = json.dumps(response.to_payload()) + json.dumps(trail.entries)

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_UNKNOWN_OPERATION
    assert "shell;rm" not in rendered
    assert "/home/example" not in rendered
    assert action.calls == []


# --- wire behaviour over the existing channel --------------------------------


def test_host_op_round_trips_over_the_existing_channel() -> None:
    config = ChannelConfig()
    host_sock, guest_sock = socket.socketpair()
    host = PeerSession(host_sock, config, PeerIdentity())
    guest = PeerSession(guest_sock, config, PeerIdentity())
    action = FakeHostActions({"broker_version": BROKER_VERSION, "ops": [READ_ONLY_OP]})
    broker = _broker({READ_ONLY_OP: action})
    try:
        guest.send(
            MessageKind.HOST_OP,
            {"version": BROKER_VERSION, "op": READ_ONLY_OP, "args": {}},
        )
        request = host.read(timeout_s=2.0)
        assert request is not None

        broker.respond(host, request, approval_granted=True)

        reply = guest.read(timeout_s=2.0)
        assert reply is not None
        assert reply.kind is MessageKind.HOST_OP
        assert reply.payload["status"] == "ok"
        assert reply.payload["op"] == READ_ONLY_OP
        assert reply.payload["request_seq"] == request.seq
        assert reply.payload["result"] == action.result
        assert payload_policy_violation(MessageKind.HOST_OP, reply.payload, config) is None
    finally:
        host.close()
        guest.close()


def test_jsonl_audit_trail_is_append_only_and_private() -> None:
    with tempfile.TemporaryDirectory(prefix="host-op-audit-") as tmp:
        path = Path(tmp) / "isolation" / "host_ops.jsonl"
        trail = JsonlHostOpAudit(path)

        trail.record({"phase": "decision", "op": READ_ONLY_OP})
        trail.record({"phase": "outcome", "op": READ_ONLY_OP})

        lines = path.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["phase"] for line in lines] == ["decision", "outcome"]
        assert (path.stat().st_mode & 0o777) == 0o600


def test_response_is_safe_even_for_a_well_formed_unknown_op() -> None:
    broker = _broker({READ_ONLY_OP: FakeHostActions()})

    response = broker.handle(_request("run_command", {"argv": ["rm", "-rf", "/"]}))

    assert response.status is HostOpStatus.DENIED
    assert response.reason == REASON_UNKNOWN_OPERATION
    assert response.op == "run_command"
    assert response.detail == ""
    assert "run_command" in json.dumps(response.to_payload())
