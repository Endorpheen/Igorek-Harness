"""Multiple blocked calls must produce one decision per actual request."""

import pytest

from test_chat_session_lifecycle_contracts import entries, prepare_turn, session as session


def blocked_write(tmp_path, name, *, context=None):
    target = str(tmp_path / name)
    return {
        "tool": "Write", "target": target, "signature": "signature-" + name,
        "input": {"file_path": target, "content": name},
        "approval_context_messages": context or [],
    }


@pytest.mark.parametrize(
    "decisions,expected",
    [
        (["allow", "allow"], ["signature-one", "signature-two"]),
        (["reject", "allow"], ["signature-two"]),
        (["allow", "reject"], ["signature-one"]),
        (["allow_session", "allow_session"], ["signature-one", "signature-two"]),
    ],
)
def test_each_blocked_call_is_asked_once_and_only_allowed_signatures_resume(
    session, monkeypatch, tmp_path, decisions, expected
):
    blocked = [blocked_write(tmp_path, "one"), blocked_write(tmp_path, "two")]
    calls = prepare_turn(session, monkeypatch, [
        {"task_id": "blocked", "status": "ok", "approval_blocked_tools": blocked},
        {"task_id": "resumed", "status": "ok", "output_text": "finished"},
    ])
    asked = []

    def decide(request):
        asked.append(request)
        assert len(asked) <= 2, "The same blocked batch was asked repeatedly"
        return decisions[len(asked) - 1]

    monkeypatch.setattr(session, "_ask_approval", decide)
    session._execute_user_turn("write two fixtures")
    assert asked == blocked
    assert len(calls) == 2 and calls[1][1]["approved_tool_calls"] == expected
    assert sum(e["role"] == "user" for e in entries(session)) == 1
    assert entries(session)[-1]["text"] == "finished"
    assert all(not (tmp_path / name).exists() for name in ["one", "two"])
    assert bool(session.approved_session_rules) == ("allow_session" in decisions)


def test_all_rejected_batch_is_not_resubmitted(session, monkeypatch, tmp_path):
    blocked = [blocked_write(tmp_path, "one"), blocked_write(tmp_path, "two")]
    calls = prepare_turn(session, monkeypatch, [
        {"task_id": "blocked", "status": "ok", "approval_blocked_tools": blocked}
    ])
    asked = []
    monkeypatch.setattr(session, "_ask_approval", lambda request: asked.append(request) or "reject")
    session._execute_user_turn("reject both fixtures")
    assert asked == blocked and len(calls) == 1
    assert sum(e["role"] == "user" for e in entries(session)) == 1
    assert not session.approved_session_rules
    assert all(not (tmp_path / name).exists() for name in ["one", "two"])


def test_all_contexts_are_merged_before_the_allowed_call_resumes(session, monkeypatch, tmp_path):
    context_one = {"role": "assistant", "content": [{"type": "tool_use", "id": "one", "name": "Write", "input": {"file_path": "one"}}]}
    context_two = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "one", "content": "blocked"}]}
    one = blocked_write(tmp_path, "one", context=[context_one])
    two = blocked_write(tmp_path, "two", context=[context_one, context_two])
    calls = prepare_turn(session, monkeypatch, [
        {"task_id": "blocked", "status": "ok", "approval_blocked_tools": [None, one, two]},
        {"task_id": "resumed", "status": "ok", "output_text": "finished"},
    ])
    asked = []
    monkeypatch.setattr(session, "_ask_approval", lambda request: asked.append(request) or ("allow" if request is one else "reject"))
    session._execute_user_turn("approve first only")
    meta = calls[1][1]
    assert asked == [one, two] and meta["approved_tool_calls"] == ["signature-one"]
    assert meta["approval_context_messages"] == [context_one, context_two]
    assert meta["forced_tool_call"]["input"] == one["input"]
    assert meta["forced_tool_call"]["approval_context_messages"] == [context_one, context_two]


@pytest.mark.parametrize("valid_host", [True, False])
def test_host_operations_keep_distinct_broker_resolution_with_tool_batch(
    session, monkeypatch, tmp_path, valid_host
):
    from factory.core.isolation.host_op_approval import HostOpApprovalRequest

    host = HostOpApprovalRequest(
        request_id="fixture-host-request", op="workspace.read", args={"path": "fixture.txt"}
    ).to_blocked_record()
    if not valid_host:
        host["args"] = {"path": []}
    tool = blocked_write(tmp_path, "one")
    calls = prepare_turn(session, monkeypatch, [
        {"task_id": "blocked", "status": "ok", "approval_blocked_tools": [host, tool]},
        {"task_id": "resumed", "status": "ok", "output_text": "finished"},
    ])
    asked = []
    monkeypatch.setattr(session, "_ask_approval", lambda request: asked.append(request) or "allow")
    session._execute_user_turn("fixture approvals")
    assert asked == [host, tool]
    meta = calls[1][1]
    assert meta["approved_tool_calls"] == ["signature-one"]
    assert meta["forced_tool_call"]["input"] == tool["input"]
    assert not session.approved_session_rules
    if valid_host:
        assert len(meta["host_op_resolutions"]) == 1
        outcome = meta["host_op_resolutions"][0]
        assert outcome["request_id"] == "fixture-host-request"
        assert outcome["decision"] == "allow_once"
    else:
        assert "host_op_resolutions" not in meta
        assert "Некорректная заявка host-op" in session.output_fixture.getvalue()


def test_malformed_batch_does_not_prompt_or_resubmit(session, monkeypatch):
    calls = prepare_turn(session, monkeypatch, [
        {"task_id": "invalid", "status": "fail", "error_message": "invalid approval payload", "approval_blocked_tools": [None, "invalid", 0]}
    ])
    monkeypatch.setattr(session, "_ask_approval", lambda _: pytest.fail("No valid approval exists"))
    session._execute_user_turn("fixture question")
    assert len(calls) == 1
    assert entries(session)[-1]["text"] == "invalid approval payload"
    assert entries(session)[-1]["status"] == "fail"
    assert not session.approved_session_rules
