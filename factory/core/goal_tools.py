"""Goal tools bound to the executing task's session and goal revision."""
from __future__ import annotations

import os

from . import thread_goals
from .tool_registry import registry, tool_error, tool_result


GET_GOAL_SCHEMA = {
    "name": "get_goal",
    "description": "Get the active session goal and its status, token budget and usage.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}
UPDATE_GOAL_SCHEMA = {
    "name": "update_goal",
    "description": (
        "Update the current goal. Mark complete only after verifying all requirements; "
        "mark blocked only when work cannot continue; pause only on the user's request."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["complete", "blocked", "paused"]}},
        "required": ["status"],
        "additionalProperties": False,
    },
}
GOAL_SCHEMAS = (GET_GOAL_SCHEMA, UPDATE_GOAL_SCHEMA)


def schemas_for_task(metadata: dict) -> list[dict]:
    if not isinstance(metadata, dict) or metadata.get("source") not in {"mobile_bridge", "cli_chat_rich"}:
        return []
    session = metadata.get("session")
    if not isinstance(session, str) or not session:
        return []
    goal = thread_goals.get_goal(session)
    if (not goal or goal["status"] != "active" or goal["goal_id"] != metadata.get("goal_id")
            or goal["revision"] != metadata.get("goal_revision")):
        return []
    return list(GOAL_SCHEMAS)


def _bound_goal(context: dict) -> tuple[str, dict]:
    session = str(context.get("current_session") or "").strip()
    metadata = context.get("delegate_metadata")
    if not session or not isinstance(metadata, dict):
        raise ValueError("goal tool requires a bound session")
    identity, revision = metadata.get("goal_id"), metadata.get("goal_revision")
    if not isinstance(identity, str) or not identity or type(revision) is not int:
        raise ValueError("goal tool requires a bound goal revision")
    goal = thread_goals.get_goal(session)
    if not goal or goal["goal_id"] != identity or goal["revision"] != revision:
        raise thread_goals.GoalConflict("the goal has changed")
    return session, goal


def get_goal(args: dict, **context) -> str:
    try:
        if args:
            raise ValueError("get_goal takes no arguments")
        provider = context.get("goal_control_provider")
        if callable(provider):
            return provider({"operation": "get"})
        if os.environ.get("IGOREK_ISOLATION_GUEST") == "1":
            raise ValueError("host goal control is unavailable")
        _, goal = _bound_goal(context)
        remaining = None if goal["token_budget"] is None else max(0, goal["token_budget"] - goal["tokens_used"])
        return tool_result({"goal": goal, "remaining_tokens": remaining})
    except (ValueError, thread_goals.GoalConflict) as exc:
        return tool_error(str(exc))


def update_goal(args: dict, **context) -> str:
    try:
        if (set(args) != {"status"} or not isinstance(args.get("status"), str)
                or args["status"] not in {"complete", "blocked", "paused"}):
            raise ValueError("status must be complete, blocked or paused")
        provider = context.get("goal_control_provider")
        if callable(provider):
            return provider({"operation": "update", "status": args["status"]})
        if os.environ.get("IGOREK_ISOLATION_GUEST") == "1":
            raise ValueError("host goal control is unavailable")
        session, goal = _bound_goal(context)
        updated = thread_goals.update_goal(session, args["status"], expected_goal_id=goal["goal_id"],
                                           expected_revision=goal["revision"])
        return tool_result({"goal": updated})
    except (ValueError, thread_goals.GoalConflict) as exc:
        return tool_error(str(exc))


for _schema, _handler in ((GET_GOAL_SCHEMA, get_goal), (UPDATE_GOAL_SCHEMA, update_goal)):
    registry.register(name=_schema["name"], toolset="goal", schema=_schema, handler=_handler)
