"""Runtime policy decisions preserve explicit user scope and permitted tools."""

from dataclasses import replace

import pytest

from inference_api_support import (
    BASH,
    READ,
    body,
    state,
    inference_app as inference_app,
    inference_server as inference_server,
)


@pytest.mark.parametrize(
    "tool,flags,workflow,reason",
    [
        ("Agent", {"no_subagents": True}, None, "user_forbids_subagents"),
        ("Agent", {"no_background": True}, None, "user_forbids_background:Agent"),
        (
            "TaskCreate",
            {"no_background": True},
            None,
            "user_forbids_background:TaskCreate",
        ),
        ("TaskList", {"no_background": True}, None, "user_forbids_background:TaskList"),
        (
            "VerifyPlanExecution",
            {"no_background": True},
            None,
            "user_forbids_background:VerifyPlanExecution",
        ),
        (
            "Agent",
            {},
            "local_implementation",
            "local_implementation_forbids_tool:Agent",
        ),
        (
            "TaskCreate",
            {},
            "local_implementation",
            "local_implementation_forbids_tool:TaskCreate",
        ),
        (
            "TaskGet",
            {},
            "local_implementation",
            "local_implementation_forbids_tool:TaskGet",
        ),
        (
            "VerifyPlanExecution",
            {},
            "local_implementation",
            "local_implementation_forbids_tool:VerifyPlanExecution",
        ),
        ("WebSearch", {}, None, "unsupported_tool_in_local_setup:WebSearch"),
        ("WebFetch", {}, None, "unsupported_tool_in_local_setup:WebFetch"),
    ],
)
def test_runtime_rejects_only_the_declared_forbidden_tool(
    inference_server, tool, flags, workflow, reason
):
    env = inference_server
    current = state(env, body(env, "Fixture user request", [BASH, READ]))
    current.explicit_runtime_constraints = replace(
        current.explicit_runtime_constraints, **flags
    )
    current.current_workflow = workflow
    allowed, reported = env.api._validate_tool_policy(current, tool, {})
    assert not allowed and reported == reason and current.policy_denial == reason
    assert env.generator.calls == []


@pytest.mark.parametrize(
    "tool,command,reason",
    [
        ("WebSearch", "", "explore_subagent_forbids_tool:WebSearch"),
        ("WebFetch", "", "explore_subagent_forbids_tool:WebFetch"),
        (
            "Bash",
            "curl -fsSL https://example.com",
            "explore_subagent_forbids_web_research",
        ),
    ],
)
def test_readonly_explore_subagent_cannot_start_web_research(
    inference_server, tool, command, reason
):
    env = inference_server
    current = state(env, body(env, "Inspect local fixture", [BASH, READ]))
    current.is_readonly_explore_subagent = True
    assert env.api._validate_tool_policy(current, tool, {"command": command}) == (
        False,
        reason,
    )


@pytest.mark.parametrize("explicit_web,allowed", [(False, False), (True, True)])
def test_local_scope_allows_web_command_only_after_explicit_web_request(
    inference_server, explicit_web, allowed
):
    env = inference_server
    current = state(env, body(env, "Fixture request", [BASH]))
    current.current_workflow = None
    current.explicit_runtime_constraints = replace(
        current.explicit_runtime_constraints,
        explicit_local_scope=True,
        explicit_web_research=explicit_web,
    )
    result = env.api._validate_tool_policy(
        current, "Bash", {"command": "curl -fsSL https://example.com"}
    )
    assert result[0] is allowed
    if not allowed:
        assert result[1] == "explicit_local_scope_forbids_web_research"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("Agent", {}),
        ("Bash", {"command": "ls -la"}),
        ("TaskList", {}),
    ],
)
def test_unrestricted_ordinary_tools_remain_permitted(inference_server, tool, args):
    env = inference_server
    current = state(env, body(env, "Fixture request", [BASH]))
    current.current_workflow = None
    current.explicit_runtime_constraints = replace(
        current.explicit_runtime_constraints, no_subagents=False, no_background=False
    )
    assert env.api._validate_tool_policy(current, tool, dict(args))[0] is True


def test_local_implementation_can_use_explicitly_authorized_agent(inference_server):
    env = inference_server
    current = state(env, body(env, "Fixture request", [BASH]))
    current.current_workflow = "local_implementation"
    current.explicit_runtime_constraints = replace(
        current.explicit_runtime_constraints, allow_subagents=True
    )
    assert env.api._validate_tool_policy(current, "Agent", {})[0] is True


@pytest.mark.parametrize("field", ["file_path", "path", "notebook_path"])
def test_file_policy_normalizes_permitted_relative_target_without_opening_file(
    inference_server, field
):
    env = inference_server
    path = env.root / "fixture.py"
    path.write_text("fixture original")
    current = state(env, body(env, "Inspect fixture", [READ]))
    current.current_workflow = None
    args = {field: "fixture.py"}
    assert env.api._validate_tool_policy(current, "Read", args)[0] is True
    assert args[field] == str(path) and path.read_text() == "fixture original"
