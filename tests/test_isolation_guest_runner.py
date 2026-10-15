"""TDD suite for the in-guest cc-backend runner (plan todo 7).

No VM and no AF_VSOCK are needed: the transport seam is an in-process
``socketpair`` driven by the real :class:`ChannelServer` handshake, and the
cc-backend loop is a stub whose only contract is the executor kwargs plus the
trace JSONL the runner tails. The real-vsock path is an env-guarded skip, never
a silent pass.

Adversarial classes covered here:

* malformed input - a bad ``task`` payload is answered with an ``error`` frame
  and a clean exit 0, never a crash and never a silently dropped task;
* misleading success - assertions check frame kinds/order and the exact result
  keys; an executor status outside the task vocabulary is not forwarded as
  success;
* resumable/long-running - ``cancel`` mid-run yields ``result{status:
  canceled}`` without waiting out the executor;
* dirty worktree - every write in this suite stays under ``tmp_path``.
"""

from __future__ import annotations

import errno
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from factory.core.cc_backend_trace import append_cc_backend_trace
from factory.core.isolation import guest_runner as guest_runner_module
from factory.core.isolation.channel import (
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    ChannelServer,
    JsonValue,
    MessageKind,
    PeerCid,
    PeerSession,
    SessionId,
    SessionMap,
    guest_connect,
)
from factory.core.isolation.egress_enforce import (
    EGRESS_TABLE_REF,
    ApplyResult,
)
from factory.core.isolation.errors import IsolationError
from factory.core.isolation.guest_runner import (
    DEFAULT_GUEST_ARTIFACT_DIR,
    DEFAULT_VSOCK_HOST_CID,
    GuestRunner,
    RunnerSeams,
    RunnerTuning,
    open_vsock_client,
)
from factory.core.isolation.guest_task import GuestTask, GuestTaskError
from factory.core.isolation.network import HOST_LOOPBACK_ALIAS

CONFIG = ChannelConfig(max_frame_bytes=64 * 1024)
CID = PeerCid(11)
SESSION = SessionId("sess-guest")
TASK_TERMINAL_KINDS = (MessageKind.RESULT, MessageKind.ERROR)
SESSION_RULESET = "# session egress ruleset\nSESSION-ALLOW-MARKER\n"
KERNEL_DENIAL_LINE = (
    "igorek-egress-blocked: IN=eth0 OUT= MAC=52:54:00:12:34:56 SRC=10.0.2.15 "
    "DST=10.0.2.2 LEN=60 PROTO=TCP SPT=43210 DPT=8001 WINDOW=64240 RES=0x00 SYN"
)


class RecordingEgress:
    """Fake applier: records every ruleset the runner enforces (plan todo 40a)."""

    def __init__(self, *, fail_on: str = "") -> None:
        self.applied: list[str] = []
        self._fail_on = fail_on

    def __call__(self, ruleset: str) -> ApplyResult:
        self.applied.append(ruleset)
        if self._fail_on and self._fail_on in ruleset:
            raise IsolationError("egress_ruleset_apply_failed", "fake refusal")
        return ApplyResult(
            applied=True,
            verified=True,
            table=EGRESS_TABLE_REF,
            ruleset_sha256="0" * 64,
            rule_count=1,
            listed_rules=1,
        )


def _egress_wire(ruleset: str = SESSION_RULESET) -> JsonValue:
    """The per-session egress section ``IsolationManager`` sends (todo 40a)."""
    return {
        "profile": "cc-wally",
        "ruleset": ruleset,
        "pinned_hosts": {HOST_LOOPBACK_ALIAS: "egress-auth-proxy"},
    }


def _task_payload(tmp_path: Path, **overrides: JsonValue) -> dict[str, JsonValue]:
    """The wire payload ``IsolationManager._wire_task_payload`` sends (todo 6)."""
    payload: dict[str, JsonValue] = {
        "payload_version": 1,
        "task_id": "task-1",
        "profile": "cc-wally",
        "model": "glm-5.3-flash",
        "base_url": "https://inference.runanywhere.ai/v1",
        "api_format": "openai",
        "system_prompt": "You are Igorek.",
        "user_message": "print hello",
        "skills_text": "",
        "foundation_context": "",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 512,
        "timeout_s": 30,
        "workdir": str(tmp_path),
        "artifact_workdir": str(tmp_path / "artifacts"),
        "trailing_context_text": "",
    }
    payload.update(overrides)
    return payload


@dataclass
class _Harness:
    """One live in-process guest session with its driver thread and outcome."""

    host: PeerSession
    thread: threading.Thread
    errors: list[BaseException]
    exit_codes: list[int]

    def collect(self, timeout_s: float = 5.0) -> list[ChannelMessage]:
        """Read frames until the terminal one; never returns an empty success."""
        messages: list[ChannelMessage] = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            message = self.host.recv_from_guest(
                timeout_s=max(0.05, deadline - time.monotonic())
            )
            if message is None:
                return messages
            messages.append(message)
            if message.kind in TASK_TERMINAL_KINDS:
                return messages
        return messages

    def finish(self) -> None:
        """Close the host side and prove the guest thread ended cleanly."""
        self.host.close()
        self.thread.join(timeout=5.0)
        assert not self.thread.is_alive()
        assert self.errors == []


def _start_harness(
    executor: Callable[..., dict[str, Any]],
    *,
    apply_egress: Callable[[str], ApplyResult] | None = None,
    kernel_log: Callable[[], tuple[str, ...]] | None = None,
    config: ChannelConfig = CONFIG,
    **tuning: Any,
) -> _Harness:
    """Run a guest endpoint in a thread; return the host end of the channel."""
    sessions = SessionMap()
    sessions.assign(CID, SESSION)
    server = ChannelServer(config, sessions)
    srv_sock, cli_sock = socket.socketpair()
    errors: list[BaseException] = []
    exit_codes: list[int] = []

    def guest() -> None:
        try:
            session = guest_connect(cli_sock, config)
            runner = GuestRunner(
                session,
                seams=RunnerSeams(
                    executor=executor,
                    apply_egress=apply_egress if apply_egress is not None else RecordingEgress(),
                    kernel_log=kernel_log if kernel_log is not None else (lambda: ()),
                ),
                tuning=RunnerTuning(guest_roots=config.guest_roots, **tuning),
            )
            try:
                exit_codes.append(runner.serve_one())
            finally:
                session.close()
        except BaseException as exc:  # reported to the test
            errors.append(exc)

    thread = threading.Thread(target=guest, daemon=True)
    thread.start()
    host = server.handle_connection(srv_sock, CID)
    assert host is not None
    return _Harness(host=host, thread=thread, errors=errors, exit_codes=exit_codes)


def test_web_tool_rpc_round_trips_through_guest_runner(tmp_path: Path) -> None:
    observed: list[str] = []

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed.append(kwargs["web_tool_provider"]("search", {"query": "safe query", "limit": 1}))
        return {"status": "ok", "output_text": observed[-1], "usage": {}, "tool_calls_count": 1}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        request = harness.host.recv_from_guest(timeout_s=10.0)
        assert request is not None and request.kind is MessageKind.WEB_REQUEST
        assert request.payload["operation"] == "search"
        assert request.payload["args"] == {"query": "safe query", "limit": 1}
        harness.host.send(MessageKind.WEB_REPLY, {
            "request_id": request.payload["request_id"], "result": "safe web result"
        })
        messages = harness.collect()
    finally:
        harness.finish()

    assert observed == ["safe web result"]
    assert messages[-1].kind is MessageKind.RESULT
    assert messages[-1].payload["output_text"] == "safe web result"


def test_session_search_rpc_round_trips_through_guest_runner(tmp_path: Path) -> None:
    observed: list[str] = []

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed.append(kwargs["session_search_provider"]({
            "query": "workspace mount", "limit": 2, "role_filter": "user,assistant", "mode": "semantic"
        }))
        return {"status": "ok", "output_text": observed[-1], "usage": {}, "tool_calls_count": 1}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        request = harness.host.recv_from_guest(timeout_s=10.0)
        assert request is not None and request.kind is MessageKind.SESSION_SEARCH_REQUEST
        assert request.payload["args"]["mode"] == "semantic"
        harness.host.send(MessageKind.SESSION_SEARCH_REPLY, {
            "request_id": request.payload["request_id"], "result": '{"success":true,"semantic_available":true}'
        })
        messages = harness.collect()
    finally:
        harness.finish()

    assert observed == ['{"success":true,"semantic_available":true}']
    assert messages[-1].kind is MessageKind.RESULT
    assert messages[-1].payload["output_text"] == observed[0]


def test_vault_store_rpc_uses_host_identity_without_transferring_the_value(tmp_path: Path) -> None:
    import json
    from factory.core.vault.agent_values import AgentValueWriter
    from factory.core.vault.lifecycle import VaultLifecycle
    from factory.core.isolation.vault_values import VaultStoreRelay
    vault = VaultLifecycle(tmp_path / 'vault')
    vault.create('fixture-password')
    config = {'name': 'fixture-model', 'model': 'model', 'base_url': 'https://example.test/v1', 'api_format': 'openai'}
    record = vault.add_credential('desktop-profile:fixture-model', 'model-key', 'api_key', imported_from='desktop-provider-setup', secret_metadata={'desktop_profile': config})
    vault.check_provider_profile(record.id, lambda *_: {'status': 'verified'})
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    (workspace / 'token.txt').write_text('fixture-private-guest-value')
    relay = VaultStoreRelay(AgentValueWriter(vault, record.id, config), workdir=str(workspace), task_id='trusted-task', session_id='trusted-session', profile=config['name'])
    observed = []
    def executor(**kwargs):
        observed.append(kwargs['vault_store_provider']({'source_path': 'token.txt', 'name': 'Guest token', 'kind': 'secret'}))
        return {'status': 'ok', 'output_text': 'Saved', 'usage': {}, 'tool_calls_count': 1}
    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        request = harness.host.recv_from_guest(timeout_s=10)
        assert request.kind is MessageKind.VAULT_STORE_REQUEST
        assert 'fixture-private-guest-value' not in json.dumps(request.payload)
        harness.host.send(MessageKind.VAULT_STORE_REPLY, relay.handle_request(request))
        messages = harness.collect()
        assert json.loads(observed[0])['entry']['origin']['task_id'] == 'trusted-task'
        assert vault.list_values()[0]['origin']['session_id'] == 'trusted-session'
        assert messages[-1].kind is MessageKind.RESULT
    finally:
        harness.finish()
        vault.lock()


def test_pending_steer_round_trips_through_guest_runner(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed.extend(kwargs["pending_steer_provider"]())
        return {"status": "ok", "output_text": "continued", "usage": {}, "tool_calls_count": 1}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(
            MessageKind.TASK, _task_payload(tmp_path, source_platform="android")
        )
        request = harness.host.recv_from_guest(timeout_s=10.0)
        assert request is not None and request.kind is MessageKind.STEER_REQUEST
        harness.host.send(
            MessageKind.STEER_REPLY,
            {
                "request_id": request.payload["request_id"],
                "items": [
                    {
                        "text": "добавь проверку",
                        "message_id": "message-1",
                        "input_files": ["/workspace/.igorek_uploads/session-a/image.png"],
                        "selected_skills": [],
                    }
                ],
            },
        )
        messages = harness.collect()
    finally:
        harness.finish()

    assert observed == [
        {
            "text": "добавь проверку",
            "message_id": "message-1",
            "input_files": ["/workspace/.igorek_uploads/session-a/image.png"],
            "selected_skills": [],
        }
    ]
    assert messages[-1].kind is MessageKind.RESULT
    assert messages[-1].payload["output_text"] == "continued"
    assert harness.errors == []


def test_task_streams_ordered_events_then_result(tmp_path: Path) -> None:
    # Given: a task whose executor records two trace events and returns ok
    observed: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = [
        {"kind": "task.start", "model": "glm-5.3-flash"},
        {"kind": "tool.result", "tool": "Write", "output_preview": "written"},
    ]

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed.append(dict(kwargs))
        for record in records:
            append_cc_backend_trace(str(kwargs["artifact_workdir"]), record)
        return {
            "status": "ok",
            "output_text": "готово",
            "usage": {"prompt": 3, "completion": 4},
            "tool_calls_count": 2,
        }

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        # When: the host dispatches one task over the channel
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the guest emitted the trace events in order, then one result frame
    assert [message.kind for message in messages] == [
        MessageKind.EVENT,
        MessageKind.EVENT,
        MessageKind.RESULT,
    ]
    emitted = [dict(message.payload["record"]) for message in messages[:2]]
    assert [record["kind"] for record in emitted] == ["task.start", "tool.result"]
    result = dict(messages[-1].payload)
    assert result["status"] == "ok"
    assert result["output_text"] == "готово"
    assert result["usage"] == {"prompt": 3, "completion": 4}
    assert result["tool_calls_count"] == 2
    # Then: the runner assembled the executor call from the payload, guarded the
    # workspace and the artifact dir, and never bypassed approvals in the guest
    assert len(observed) == 1
    kwargs = observed[0]
    assert kwargs["workdir"] == str(tmp_path)
    assert kwargs["artifact_workdir"] == str(tmp_path / "artifacts")
    assert kwargs["max_tokens"] == 512
    assert kwargs["model"] == "glm-5.3-flash"
    assert kwargs["profile"] == "cc-wally"
    assert kwargs["system_prompt"] == "You are Igorek."
    assert kwargs["user_message"] == "print hello"
    assert kwargs["approvals_enabled_provider"]() is True
    # And: the user-input seam is the in-guest bridge (todo 10), never a terminal
    assert callable(kwargs["user_input_provider"])
    assert (tmp_path / "artifacts").is_dir()
    assert harness.exit_codes == [0]


def test_cancel_mid_run_yields_canceled_result(tmp_path: Path) -> None:
    # Given: an executor that keeps working until the runner cancels it
    started = threading.Event()

    def executor(**kwargs: Any) -> dict[str, Any]:
        started.set()
        while True:
            kwargs["log_fn"]("executor tick")
            time.sleep(0.005)

    harness = _start_harness(executor, poll_interval_s=0.01, cancel_grace_s=0.5)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        assert started.wait(3.0)
        # When: the host cancels mid-run
        harness.host.send(MessageKind.CANCEL, {"reason": "user"})
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the run is aborted and answered with a canceled result
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    result = dict(messages[0].payload)
    assert result["status"] == "canceled"
    assert result["output_text"] == ""
    assert result["usage"] == {}
    assert harness.exit_codes == [0]


def test_malformed_task_yields_error_frame_and_clean_exit(tmp_path: Path) -> None:
    # Given: a host that dispatches a task payload missing every required field
    calls: list[dict[str, Any]] = []

    def executor(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {"status": "ok", "output_text": "", "usage": {}, "tool_calls_count": 0}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        # When: the host sends a malformed task
        harness.host.send(MessageKind.TASK, {"task_id": "task-1"})
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: one error frame names the stable reason, the executor never ran and
    # the guest process exits 0 (no crash, no silent drop)
    assert [message.kind for message in messages] == [MessageKind.ERROR]
    reason = str(dict(messages[0].payload).get("reason") or "")
    assert reason.startswith("task_missing:")
    assert calls == []
    assert harness.exit_codes == [0]


def test_task_without_payload_version_runs_as_legacy(tmp_path: Path) -> None:
    # Given: the committed host manager does not send payload_version yet
    def executor(**kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "output_text": "ok", "usage": {}, "tool_calls_count": 0}

    payload = _task_payload(tmp_path)
    del payload["payload_version"]
    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, payload)
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the legacy payload is accepted and answered with a result
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    assert dict(messages[0].payload)["status"] == "ok"
    assert harness.exit_codes == [0]


def test_unsupported_payload_version_is_refused(tmp_path: Path) -> None:
    # Given: a task from a future wire version the guest cannot interpret
    def executor(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("the executor must not run for an unsupported version")

    payload = _task_payload(tmp_path, payload_version=99)
    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, payload)
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the guest refuses with a stable reason and a clean exit
    assert [message.kind for message in messages] == [MessageKind.ERROR]
    assert dict(messages[0].payload)["reason"] == "payload_version_unsupported:99"
    assert harness.exit_codes == [0]


def test_unexpected_frame_before_task_is_refused(tmp_path: Path) -> None:
    # Given: a host that cancels before ever sending a task
    def executor(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("no executor run without a task")

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.CANCEL, {"reason": "user"})
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: one error frame and a clean exit instead of a crash
    assert [message.kind for message in messages] == [MessageKind.ERROR]
    assert dict(messages[0].payload)["reason"] == "unexpected_frame:cancel"
    assert harness.exit_codes == [0]


def test_event_denied_by_payload_policy_is_skipped_and_stream_continues(
    tmp_path: Path,
) -> None:
    # Given: an executor that emits one policy-denied trace event (a whole-value
    # host path, which the todo-8 display envelope still refuses) between two
    # clean ones. Shell-key tool input is no longer a violation: the record
    # crosses inside the inert display envelope.
    def executor(**kwargs: Any) -> dict[str, Any]:
        artifact = str(kwargs["artifact_workdir"])
        append_cc_backend_trace(artifact, {"kind": "task.start", "round": 0})
        append_cc_backend_trace(
            artifact,
            {
                "kind": "tool.result",
                "tool_name": "Bash",
                "raw_result_preview": "/home/example/secret",
            },
        )
        append_cc_backend_trace(artifact, {"kind": "tool.result", "tool": "Bash"})
        return {"status": "ok", "output_text": "ok", "usage": {}, "tool_calls_count": 1}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the denied event is dropped, the stream keeps serving in order
    assert [message.kind for message in messages] == [
        MessageKind.EVENT,
        MessageKind.EVENT,
        MessageKind.RESULT,
    ]
    emitted = [str(dict(message.payload["record"]).get("kind")) for message in messages[:2]]
    assert emitted == ["task.start", "tool.result"]
    assert dict(messages[-1].payload)["status"] == "ok"


def test_executor_error_becomes_fail_result(tmp_path: Path) -> None:
    # Given: an executor that raises mid-task
    def executor(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the failure is reported as a fail result, not swallowed and not ok
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    result = dict(messages[0].payload)
    assert result["status"] == "fail"
    assert result["failure_reason"] == "executor_error"
    assert "boom" in str(result["failure_details"])
    assert harness.exit_codes == [0]


@pytest.mark.parametrize(
    ("make_error", "token"),
    [
        pytest.param(
            lambda: OSError(errno.ENOSPC, "No space left on device"),
            "enospc",
            id="disk-enospc",
        ),
        pytest.param(
            lambda: OSError(errno.EDQUOT, "Disk quota exceeded"),
            "disk_quota_exceeded",
            id="disk-edquot",
        ),
        pytest.param(
            lambda: OSError(errno.ENOMEM, "Cannot allocate memory"),
            "cannot_allocate_memory",
            id="memory-enomem",
        ),
        pytest.param(lambda: MemoryError(), "oom", id="memory-error"),
        pytest.param(
            lambda: OSError(errno.EMFILE, "Too many open files"),
            "emfile",
            id="fd-emfile",
        ),
        pytest.param(
            lambda: BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable"),
            "fork_failed",
            id="pids-eagain",
        ),
    ],
)
def test_executor_resource_exhaustion_is_reported_as_a_classified_error_frame(
    tmp_path: Path, make_error: Callable[[], BaseException], token: str
) -> None:
    # Given: an executor that dies with a real resource-exhaustion exception
    def executor(**kwargs: Any) -> dict[str, Any]:
        raise make_error()

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        # When: the host dispatches one task
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the guest reports the documented limit token as a classified error
    # frame (the host classifies that token and contains the VM), never a
    # generic fail result that would leave the VM running
    assert [message.kind for message in messages] == [MessageKind.ERROR]
    assert dict(messages[0].payload)["reason"] == token
    assert harness.exit_codes == [0]


def test_non_limit_executor_oserror_still_becomes_fail_result(tmp_path: Path) -> None:
    # Given: an executor that fails with an errno that is not a limit breach
    def executor(**kwargs: Any) -> dict[str, Any]:
        raise OSError(errno.EACCES, "Permission denied")

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the compatibility path is unchanged - a plain fail result
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    result = dict(messages[0].payload)
    assert result["status"] == "fail"
    assert result["failure_reason"] == "executor_error"


def test_stale_trace_records_are_not_replayed(tmp_path: Path) -> None:
    # Given: an artifact dir that still holds a previous run's trace file
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    append_cc_backend_trace(str(artifact), {"kind": "task.start", "round": "stale"})
    append_cc_backend_trace(str(artifact), {"kind": "task.finish", "status": "ok"})

    def executor(**kwargs: Any) -> dict[str, Any]:
        append_cc_backend_trace(str(kwargs["artifact_workdir"]), {"kind": "tool.result"})
        return {"status": "ok", "output_text": "ok", "usage": {}, "tool_calls_count": 1}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: only this run's record crosses; the stale ones stay in the guest
    assert [message.kind for message in messages] == [
        MessageKind.EVENT,
        MessageKind.RESULT,
    ]
    record = dict(messages[0].payload["record"])
    assert record["kind"] == "tool.result"


def test_blocked_tool_result_survives_the_payload_policy(tmp_path: Path) -> None:
    # Given: an executor result whose blocked-tool payload carries a raw shell
    # string under a key the host payload policy refuses
    def executor(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "waiting_approval",
            "output_text": "approval needed",
            "usage": {"prompt": 1},
            "tool_calls_count": 1,
            "failure_reason": "approval_required",
            "approval_blocked_tools": [
                {
                    "tool": "Bash",
                    "input": {"command": "rm -rf /"},
                    "target": "rm -rf /",
                    "tool_use_id": "call_blocked",
                }
            ],
        }

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the status stays visible to the host instead of a rejected frame,
    # and the blocked payload crosses in the dedicated inert envelope (todo 9)
    assert [message.kind for message in messages] == [
        MessageKind.APPROVAL_REQUEST,
        MessageKind.RESULT,
    ]
    envelope = dict(messages[0].payload)
    assert dict(envelope["record"])["tool"] == "Bash"
    assert dict(envelope["record"])["input"] == {"command": "rm -rf /"}
    result = dict(messages[1].payload)
    assert result["status"] == "waiting_approval"
    assert result["failure_reason"] == "approval_required"
    assert "approval_blocked_tools" not in result


def test_unknown_executor_status_is_reported_as_failure(tmp_path: Path) -> None:
    # Given: an executor that returns a status outside the task vocabulary
    def executor(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "output_text": "looks fine",
            "usage": {},
            "tool_calls_count": 1,
        }

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the misleading success is never forwarded as an ok result
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    result = dict(messages[0].payload)
    assert result["status"] == "fail"
    assert result["failure_reason"] == "invalid_executor_result"


def test_workdir_outside_guest_roots_is_refused() -> None:
    # Given/When/Then: the payload boundary refuses a host path as the workdir
    payload = {"workdir": "/etc", "artifact_workdir": "/tmp/igorek-artifacts"}
    with pytest.raises(GuestTaskError) as refusal:
        GuestTask.from_wire(payload, guest_roots=CONFIG.guest_roots)
    assert refusal.value.reason == "task_workdir_denied"


def test_payload_version_must_be_an_integer() -> None:
    # Given/When/Then: a non-integer version is a malformed task, not a guess
    payload = {"payload_version": "1", "workdir": "/tmp/ws", "artifact_workdir": "/tmp/igorek-artifacts"}
    with pytest.raises(GuestTaskError) as refusal:
        GuestTask.from_wire(payload, guest_roots=CONFIG.guest_roots)
    assert refusal.value.reason == "task_invalid:payload_version"


def test_guest_artifact_dir_matches_the_manager_contract() -> None:
    # Given/When/Then: the guest artifact dir never drifts from the host manager
    from factory.core.isolation import manager

    assert DEFAULT_GUEST_ARTIFACT_DIR == manager.GUEST_ARTIFACT_DIR


def test_vsock_client_fails_closed_without_af_vsock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a guest kernel without AF_VSOCK
    monkeypatch.setattr(socket, "AF_VSOCK", None, raising=False)
    # When/Then: the connect helper refuses instead of opening a wrong socket
    with pytest.raises(ChannelError) as refusal:
        open_vsock_client(DEFAULT_VSOCK_HOST_CID, 5005)
    assert refusal.value.reason == "vsock_unavailable"


def test_cli_registers_guest_runner_without_breaking_existing_commands() -> None:
    # Given: the unified CLI parser
    from factory.cli import _build_parser

    parser = _build_parser()
    # When/Then: the new subcommand parses and forwards its own flags to the module
    args, rest = parser.parse_known_args(["guest-runner", "--vsock-cid", "3"])
    assert args.command == "guest-runner"
    assert rest == ["--vsock-cid", "3"]
    # Then: the existing subcommands still parse unchanged
    known, _ = parser.parse_known_args(["env-help"])
    assert known.command == "env-help"
    known, _ = parser.parse_known_args(["gateway", "--profile", "cc-wally"])
    assert known.command == "gateway"


def test_runner_module_exposes_no_host_side_writes() -> None:
    # Given/When/Then: the guest module never imports the host-side manager (it
    # must not drag libvirt/preflight/probes into the guest)
    source = Path(guest_runner_module.__file__).read_text(encoding="utf-8")
    assert "isolation.manager" not in source
    assert "isolation.preflight" not in source
    assert "isolation.domain" not in source


def test_runner_enforces_the_per_session_ruleset_before_the_executor(
    tmp_path: Path,
) -> None:
    # Given: a v2 task carrying the host-rendered per-session ruleset
    applier = RecordingEgress()
    observed: list[str] = []

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed.append("executor")
        return {"status": "ok", "output_text": "", "usage": {}, "tool_calls_count": 0}

    harness = _start_harness(executor, apply_egress=applier, poll_interval_s=0.01)
    try:
        harness.host.send(
            MessageKind.TASK,
            _task_payload(
                tmp_path,
                payload_version=2,
                approval_policy="host_resolved",
                task_metadata={},
                egress=_egress_wire(),
            ),
        )
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the guest applied exactly that ruleset, after the boot baseline and
    # before any executor code ran
    assert applier.applied[-1] == SESSION_RULESET
    assert len(applier.applied) == 2, applier.applied
    assert "policy drop" in applier.applied[0]
    assert observed == ["executor"]
    assert [message.kind for message in messages] == [MessageKind.RESULT]
    assert harness.exit_codes == [0]


def test_runner_applies_the_boot_baseline_when_the_task_carries_no_ruleset(
    tmp_path: Path,
) -> None:
    # Given: a legacy payload without the per-session egress section
    applier = RecordingEgress()

    def executor(**kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "output_text": "", "usage": {}, "tool_calls_count": 0}

    harness = _start_harness(executor, apply_egress=applier, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        harness.collect()
    finally:
        harness.finish()

    # Then: the guest never runs on the QEMU user-net defaults
    assert len(applier.applied) == 1
    assert "policy drop" in applier.applied[0]
    assert "ip daddr 10.0.2.2 log prefix" in applier.applied[0]


def test_runner_refuses_to_run_when_the_session_ruleset_is_not_enforced(
    tmp_path: Path,
) -> None:
    # Given: an applier that cannot install the session ruleset
    applier = RecordingEgress(fail_on="SESSION-ALLOW-MARKER")
    observed: list[str] = []

    def executor(**kwargs: Any) -> dict[str, Any]:
        observed.append("executor")
        return {"status": "ok", "output_text": "", "usage": {}, "tool_calls_count": 0}

    harness = _start_harness(executor, apply_egress=applier, poll_interval_s=0.01)
    try:
        harness.host.send(
            MessageKind.TASK,
            _task_payload(
                tmp_path,
                payload_version=2,
                approval_policy="host_resolved",
                task_metadata={},
                egress=_egress_wire(),
            ),
        )
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: fail-closed - an error frame, no executor run, no silent widening
    assert [message.kind for message in messages] == [MessageKind.ERROR]
    assert "egress_ruleset_apply_failed" in str(messages[0].payload["reason"])
    assert observed == []
    assert harness.exit_codes == [0]


def test_runner_emits_blocked_host_events_from_the_kernel_log(tmp_path: Path) -> None:
    # Given: a running turn and a kernel log that carries a deny line
    polls = {"count": 0}

    def kernel_lines() -> tuple[str, ...]:
        polls["count"] += 1
        return (KERNEL_DENIAL_LINE,) if polls["count"] == 2 else ()

    def executor(**kwargs: Any) -> dict[str, Any]:
        time.sleep(0.25)
        return {"status": "ok", "output_text": "", "usage": {}, "tool_calls_count": 0}

    harness = _start_harness(
        executor, kernel_log=kernel_lines, poll_interval_s=0.01
    )
    try:
        harness.host.send(
            MessageKind.TASK,
            _task_payload(
                tmp_path,
                payload_version=2,
                approval_policy="host_resolved",
                task_metadata={},
                egress=_egress_wire(),
            ),
        )
        messages = harness.collect()
    finally:
        harness.finish()

    # Then: the blocked probe became an isolation.blocked_host event frame
    events = [
        dict(message.payload["record"])
        for message in messages
        if message.kind is MessageKind.EVENT
        and isinstance(message.payload.get("record"), dict)
    ]
    blocked = [record for record in events if record.get("kind") == "isolation.blocked_host"]
    assert len(blocked) == 1, events
    assert blocked[0]["destination"] == "10.0.2.2"
    assert blocked[0]["port"] == 8001
    assert blocked[0]["pinned_host"] == "egress-auth-proxy"
    assert messages[-1].kind is MessageKind.RESULT


def test_real_vsock_guest_connect_smoke() -> None:
    # Given: a guest VM with a host-side manager listening on the vsock port
    if os.environ.get("IGOREK_ISOLATION_VSOCK_SMOKE") != "1":
        pytest.skip(
            "real vsock smoke: run inside a session VM with "
            "IGOREK_ISOLATION_VSOCK_SMOKE=1 (needs a live host listener)"
        )
    # When: the guest connects and handshakes
    sock = open_vsock_client(DEFAULT_VSOCK_HOST_CID, ChannelConfig().port)
    try:
        session = guest_connect(sock, ChannelConfig())
    finally:
        sock.close()
    # Then: the host mapped the CID and answered with the session id
    assert session.session_id is not None


def test_heartbeat_frames_flow_during_a_quiet_executor_and_stop_before_result(
    tmp_path: Path,
) -> None:
    # Given: a heartbeat interval shorter than a quiet executor runtime
    def executor(**kwargs: Any) -> dict[str, Any]:
        time.sleep(0.3)
        return {"status": "ok", "output_text": "done", "usage": {}, "tool_calls_count": 0}

    harness = _start_harness(
        executor, poll_interval_s=0.01, heartbeat_interval_s=0.05
    )
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect(timeout_s=5.0)
    finally:
        harness.finish()

    # Then: liveness frames flowed, every one was inert, and RESULT stayed last
    kinds = [message.kind for message in messages]
    assert MessageKind.HEARTBEAT in kinds
    assert kinds[-1] is MessageKind.RESULT
    assert kinds.index(MessageKind.RESULT) == len(kinds) - 1
    beats = [m for m in messages if m.kind is MessageKind.HEARTBEAT]
    assert all(dict(beat.payload) == {"state": "alive"} for beat in beats)


def test_long_quiet_executor_survives_the_channel_idle_timeout(tmp_path: Path) -> None:
    # Given: a channel whose idle window is far shorter than the tool runtime
    config = ChannelConfig(max_frame_bytes=64 * 1024, io_timeout_s=0.2)

    def executor(**kwargs: Any) -> dict[str, Any]:
        time.sleep(0.5)
        return {"status": "ok", "output_text": "done", "usage": {}, "tool_calls_count": 0}

    # When: the executor stays silent past the idle window, then finishes
    harness = _start_harness(executor, config=config, poll_interval_s=0.01)
    try:
        harness.host.send(MessageKind.TASK, _task_payload(tmp_path))
        messages = harness.collect(timeout_s=5.0)
    finally:
        harness.finish()

    # Then: the task completes normally instead of being canceled by the idle timeout
    assert messages[-1].kind is MessageKind.RESULT
    assert messages[-1].payload["status"] == "ok"
