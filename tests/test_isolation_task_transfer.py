"""Oversized history reaches the guest intact without oversized channel frames."""
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.isolation.channel import ChannelConfig, ChannelError, write_frame, read_frame
from factory.core.isolation.task_transfer import pack_task, unpack_task
from factory.core.isolation.guest_task import GuestTask, GuestTaskError
from factory.core.isolation.history import build_wire_task_payload, HostTaskInput


def payload(root, history):
    return build_wire_task_payload(HostTaskInput(
        task_id="task-123", profile="cc-test", model="test", base_url="http://localhost/v1",
        api_format="openai", system_prompt="rules", user_message="continue",
        skills_text="", foundation_context="", filesystem_mode="workspace", guard_mode="on",
        max_tokens=1024, timeout_s=60, workdir=str(root),
        artifact_workdir=str(root / ".igorek-artifacts" / "task-123"),
        history_messages=tuple(history),
    ))


def test_large_task_round_trip_through_real_socket_and_guest_parser(tmp_path):
    history = [{"role": "assistant", "content": "verified output " + "я" * 4_000_000, "history_id": "tool:old:1"}]
    wire = payload(tmp_path, history)
    with patch("factory.core.isolation.task_transfer.ChannelConfig", return_value=ChannelConfig(guest_roots=(str(tmp_path),))):
        envelope = pack_task(wire, host_root=str(tmp_path))
        assert len(json.dumps(envelope).encode()) < 4096
        left, right = socket.socketpair()
        try:
            write_frame(left, {"kind": "task", "payload": envelope})
            received = read_frame(right)
            task = GuestTask.from_wire(received["payload"], guest_roots=(str(tmp_path),))
        finally:
            left.close(); right.close()
    assert list(task.history_messages) == history
    assert task.executor_kwargs(lambda _: None)["history_messages"] == history


def test_small_task_stays_inline(tmp_path):
    wire = payload(Path('/workspace'), [])
    assert pack_task(wire, host_root=str(tmp_path)) is wire
    assert unpack_task(wire) is wire


@pytest.mark.parametrize('mutation', ['digest', 'missing', 'size', 'path', 'task', 'symlink'])
def test_invalid_shared_payload_is_refused(tmp_path, mutation):
    wire = payload(tmp_path, [{"role": "user", "content": "x" * 1_100_000}])
    from factory.core.isolation.channel import ChannelConfig
    with patch("factory.core.isolation.task_transfer.ChannelConfig", return_value=ChannelConfig(guest_roots=(str(tmp_path),))):
        envelope = pack_task(wire, host_root=str(tmp_path))
        path = Path(envelope['task_payload_file']['path'])
        if mutation == 'digest':
            body = path.read_bytes();path.write_bytes(body[:-1] + b' ')
        elif mutation == 'missing': path.unlink()
        elif mutation == 'size': envelope['task_payload_file']['size'] += 1
        elif mutation == 'path': envelope['task_payload_file']['path'] = str(tmp_path / 'outside')
        elif mutation == 'task': envelope['task_id'] = 'other'
        elif mutation == 'symlink':
            other = tmp_path / 'other';path.rename(other);path.symlink_to(other)
        with pytest.raises(GuestTaskError, match='task_payload_file_invalid'):
            GuestTask.from_wire(envelope, guest_roots=(str(tmp_path),))


def test_oversized_frame_fails_before_writing_any_bytes():
    class Socket:
        def sendall(self, data):
            raise AssertionError('must not write oversized frame')
    with pytest.raises(ChannelError, match='frame_too_large'):
        write_frame(Socket(), {'payload': 'x' * 100}, max_frame_bytes=50)


def test_ephemeral_mcp_credentials_are_not_persisted(tmp_path):
    wire = payload(tmp_path, [{"role": "user", "content": "x" * 1_100_000}])
    wire["mcp_servers"] = [{"name": "ctx", "program": "node", "args": [], "env": {"TOKEN": "ephemeral-only"}}]
    with patch("factory.core.isolation.task_transfer.ChannelConfig", return_value=ChannelConfig(guest_roots=(str(tmp_path),))):
        envelope = pack_task(wire, host_root=str(tmp_path))
        assert 'ephemeral-only' not in Path(envelope['task_payload_file']['path']).read_text()
        assert unpack_task(envelope) == wire
