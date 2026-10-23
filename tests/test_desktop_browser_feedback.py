import json

import pytest

from factory.core.desktop_browser import DesktopBrowserPipe, SCHEMA


@pytest.mark.parametrize("fields", [
    {"action": "read", "limit": 1500},
    {"action": "read", "offset": 9000, "limit": 6000},
    {"action": "collect", "limit": 20},
    {"action": "result", "limit": 6000},
    {"action": "scroll", "direction": "down"},
    {"action": "press", "key": "PageUp"},
    {"action": "click", "target": "old-ref"},
])
def test_rejected_browser_arguments_have_actionable_feedback_without_execution(fields):
    emitted = []
    pipe = DesktopBrowserPipe(emitted.append, lambda _: True, timeout=.01)
    result = json.loads(pipe.call(fields))
    assert result["error"] == "invalid_request"
    assert result["hint"]
    assert fields["action"] in result["hint"]
    diagnostic = result["diagnostic"]
    assert diagnostic["code"] == "TOOL.INVALID_ARGUMENTS"
    assert diagnostic["delivery"] == "rejected"
    assert diagnostic["outcome"] == "failed"
    assert diagnostic["reason_detail"]
    assert emitted == []
    assert pipe.pending == {}


@pytest.mark.parametrize("reason", ["invalid_url", "stale_target"])
def test_desktop_pre_execution_rejections_keep_the_reason_and_add_recovery_guidance(reason):
    def emit(frame):
        pipe.receive({"type": "browser_reply", "id": frame["id"], "result": {"error": reason}})

    pipe = DesktopBrowserPipe(emit, lambda _: True)
    fields = ({"action": "read_post", "url": "https://x.com/fixture/status/123/history"}
              if reason == "invalid_url" else {"action": "click", "target": "r-" + "a" * 32 + "-1"})
    result = json.loads(pipe.call(fields))
    assert result["error"] == reason
    assert result["diagnostic"]["code"] == "TOOL.INVALID_ARGUMENTS"
    assert result["diagnostic"]["reason_code"] == reason
    assert result["diagnostic"]["delivery"] == "rejected"
    assert ("read" if reason == "stale_target" else "URL") in result["hint"]


@pytest.mark.parametrize("reply", [
    {"error": "post_not_found"},
    {"error": "navigation_timeout", "performed": True},
    {"error": "stale_target", "performed": True},
    {"error": "desktop_browser_access_denied"},
    {"error": "invalid_request", "diagnostic": {"code": "TOOL.EXECUTION_FAILED", "outcome": "unknown"}},
    {"performed": True, "posts": []},
])
def test_execution_failures_and_successes_are_not_reclassified_as_rejected(reply):
    def emit(frame):
        pipe.receive({"type": "browser_reply", "id": frame["id"], "result": reply})

    pipe = DesktopBrowserPipe(emit, lambda _: True)
    assert json.loads(pipe.call({"action": "read"})) == reply


def test_feedback_does_not_echo_rejected_values_or_arbitrary_keys():
    pipe = DesktopBrowserPipe(lambda _: pytest.fail("rejected call reached desktop"), lambda _: True)
    result = pipe.call({"action": "read", "private-CANARY": "CANARY"})
    assert "CANARY" not in result
    result = pipe.call({"action": "CANARY"})
    assert "CANARY" not in result


def test_flat_schema_explains_action_specific_parameters_for_multiple_llm_transports():
    description = SCHEMA["description"]
    assert "action only" in description
    assert "read_post" in SCHEMA["input_schema"]["properties"]["limit"]["description"]
    assert "scroll_element" in SCHEMA["input_schema"]["properties"]["direction"]["description"]
    assert "target" in SCHEMA["input_schema"]["properties"]["key"]["description"]


@pytest.mark.parametrize("fields, field", [
    ({"action": "comments", "url": "https://x.com/fixture/status/123", "limit": 21}, "limit"),
    ({"action": "read_post", "url": "https://x.com/fixture/status/123", "offset": -1}, "offset"),
    ({"action": "wait", "timeout_ms": True}, "timeout_ms"),
    ({"action": "elements", "query": []}, "query"),
    ({"action": "fill", "target": "r-" + "a" * 32 + "-0", "text": "x" * 16001}, "text"),
    ({"action": "check", "target": "r-" + "a" * 32 + "-0", "checked": "true"}, "checked"),
    ({"action": "click", "target": "r-" + "a" * 32 + "-0", "offset_x": float("nan")}, "offset_x"),
])
def test_invalid_values_identify_the_parameter_without_echoing_its_contents(fields, field):
    pipe = DesktopBrowserPipe(lambda _: pytest.fail("invalid value reached desktop"), lambda _: True)
    result = json.loads(pipe.call(fields))
    assert result["error"] == "invalid_request"
    assert field in result["diagnostic"]["reason_detail"]
    assert field in result["hint"]
