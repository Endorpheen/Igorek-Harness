"""Delegate Tool — Subagent Architecture for factory cc-backend.

Spawns child agent runs with isolated context, restricted toolsets,
and their own iteration budget.  The parent blocks until the child
completes.

Adapted from Hermes tools/delegate_tool.py to our
execute_cc_backend_task() loop.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

# Tools that children must never have access to
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",   # removed at the deepest supported level
])

MAX_CONCURRENT_CHILDREN = 3
MAX_DEPTH = 2  # parent (0) -> child (1) -> grandchild (2)
DEFAULT_MAX_ITERATIONS = 50


def check_delegate_requirements() -> bool:
    """Delegation is always available."""
    return True


def _strip_blocked_tools(tool_names: list[str]) -> list[str]:
    return [t for t in tool_names if t not in DELEGATE_BLOCKED_TOOLS]


def _build_child_system_prompt(goal: str, context: str | None, workdir: str) -> str:
    parts = [
        "You are a focused subagent working on a specific delegated task.",
        "",
        f"YOUR TASK:\n{goal}",
    ]
    if context and context.strip():
        parts.append(f"\nCONTEXT:\n{context}")
    parts.append(
        f"\nWORKSPACE PATH:\n{workdir}\n"
        "Use this exact path for local file operations unless the task says otherwise."
    )
    parts.append(
        "\nComplete this task using the tools available to you. "
        "When finished, provide a clear, concise summary of what you did, "
        "what you found, any files created or modified, and any issues encountered. "
        "Your response is returned to the parent agent as a summary."
    )
    return "\n".join(parts)


def delegate_task(args: dict[str, Any], **kwargs: Any) -> str:
    """Spawn a child agent to handle a delegated task."""
    # Import here to avoid circular dependency at module load time.
    from .cc_backend_executor import execute_cc_backend_task
    from .prompt_assembly import build_cc_backend_system_prompt
    from .subagents import (
        STATUS_CANCELLED,
        STATUS_ERROR,
        STATUS_OK,
        SubagentReporter,
        cancel_requested,
        new_subagent_id,
        subagents_cancel_dir,
    )
    from .cc_backend_tools import get_tool_schemas
    from .iteration_budget import IterationBudget

    parent_workdir = str(kwargs.get("workdir") or "")
    delegate_depth = int(kwargs.get("delegate_depth") or 0)
    base_url = str(kwargs.get("base_url") or "http://127.0.0.1:8001")
    model = str(kwargs.get("model") or "")
    filesystem_mode = str(kwargs.get("filesystem_mode") or "workspace")
    guard_mode = kwargs.get("guard_mode")
    current_session = kwargs.get("current_session")
    source_platform = kwargs.get("source_platform")
    source_chat_id = kwargs.get("source_chat_id")
    max_tokens = int(kwargs.get("max_tokens") or 4000)
    timeout_s = int(kwargs.get("timeout_s") or 300)
    parent_task_id = str(kwargs.get("parent_task_id") or "").strip() or str(
        kwargs.get("task_id") or ""
    ).strip()
    progress_callback = kwargs.get("progress_callback")
    interrupt_event = kwargs.get("interrupt_event")
    # Durable observability for a sub-agent: task log and parent tool call identifier
    # (linking the card in the web interface with delegate_task).
    artifact_workdir = str(kwargs.get("artifact_workdir") or "").strip() or parent_workdir
    tool_use_id = str(kwargs.get("tool_use_id") or "").strip()
    profile = str(kwargs.get("profile") or "")
    # The child execution transport must match the parent’s: otherwise, a profile with an OpenAI-compatible endpoint (e.g., cc-opencode-go) will send the child to an Anthropic path `/messages` and return a 404 HTML response. The `api_format` comes from the `tool-dispatch` executor; omitting the value retains the default.
    api_format = str(kwargs.get("api_format") or "anthropic").strip().lower() or "anthropic"
    request_headers = kwargs.get("request_headers")
    if not isinstance(request_headers, dict):
        request_headers = None

    # Depth limit
    if delegate_depth >= MAX_DEPTH:
        return json.dumps({
            "error": (
                f"Delegation depth limit reached ({MAX_DEPTH}). "
                "Subagents cannot spawn further subagents."
            )
        }, ensure_ascii=False)

    goal = str(args.get("goal") or "").strip()
    context = str(args.get("context") or "").strip() or None
    toolsets = args.get("toolsets")
    max_iterations = args.get("max_iterations")

    if not goal:
        return json.dumps(
            {"error": "Provide 'goal' for the subagent."},
            ensure_ascii=False,
        )

    # Build isolated child workdir
    resume = args.get("_resume") if isinstance(args.get("_resume"), dict) else {}
    child_id = str(resume.get("child_id") or uuid.uuid4().hex[:8])
    if len(child_id) != 8 or any(char not in "0123456789abcdef" for char in child_id):
        return json.dumps({"error": "Invalid child identity"})
    child_workdir = os.path.join(parent_workdir, f".delegate_{child_id}")
    os.makedirs(child_workdir, exist_ok=True)

    # Stable identity for the child execution: while the parent is blocked, the web interface should display the execution itself, its link to the task/tool call, and the execution progress.
    subagent_id = str(resume.get("subagent_id") or new_subagent_id())
    if not subagent_id.startswith("sub_") or not subagent_id[4:].isalnum():
        return json.dumps({"error": "Invalid agent identity"})
    reporter = SubagentReporter(
        artifact_workdir=artifact_workdir,
        session_id=kwargs.get("current_session"),
        parent_task_id=parent_task_id,
    )
    reporter.start(
        subagent_id=subagent_id,
        tool_use_id=tool_use_id,
        goal=goal,
        depth=delegate_depth + 1,
        model=model,
        profile=profile,
        workdir=child_workdir,
        goal_text=goal,
    )

    def _child_progress(stage: str, **fields: Any) -> None:
        """Logs the child execution progress into the durable sub-agent log.

        Only structural stages (start/end of the tool) are written, not
        every model delta—otherwise, the log swells excessively.
        """
        tool_calls = fields.get("tool_calls_count")
        reporter.progress(
            subagent_id=subagent_id,
            stage=str(stage),
            tool_name=str(fields.get("name") or fields.get("tool_name") or ""),
            tool_calls_count=int(tool_calls) if isinstance(tool_calls, int) else None,
        )
        if progress_callback:
            try:
                progress_callback(stage, **fields)
            except Exception:
                pass

    parent_input_provider = kwargs.get("user_input_provider")
    def child_input_provider(payload):
        _child_progress("waiting_input")
        try:
            return parent_input_provider(payload)
        finally:
            _child_progress("running")

    # Resolve available tools and filter
    from .tool_registry import registry
    from .toolsets import resolve_toolset
    available = kwargs.get("delegate_available_tools")
    all_schemas = available if isinstance(available, list) else get_tool_schemas()
    all_names = {s.get("name") for s in all_schemas if s.get("name")}
    requested: set[str] = set()
    if isinstance(toolsets, list):
        unknown = []
        for t in toolsets:
            if not isinstance(t, str):
                unknown.append(str(t))
                continue
            grouped = set(resolve_toolset(t))
            grouped.update(name for name in all_names if registry.get_toolset_for_tool(name) == t)
            if t in {"all", "*"}:
                requested.update(all_names)
            elif t in all_names:
                requested.add(t)
            elif grouped:
                requested.update(grouped)
            else:
                unknown.append(t)
        if unknown:
            reporter.finish(subagent_id=subagent_id, status=STATUS_ERROR, error="Unknown tool selection: " + ", ".join(unknown))
            return json.dumps({"status": "fail", "failure_reason": "invalid_subagent_tools", "error": "Unknown tool selection", "unknown_tools": unknown, "subagent_id": subagent_id})
    if not requested:
        requested = set(all_names)
    child_names = _strip_blocked_tools(sorted(requested & all_names))
    if delegate_depth + 1 < MAX_DEPTH and "delegate_task" in requested & all_names:
        child_names.append("delegate_task")
    child_schemas = [s for s in all_schemas if s.get("name") in child_names]
    if requested and not child_schemas:
        reporter.finish(subagent_id=subagent_id, status=STATUS_ERROR, error="No selected tools are available")
        return json.dumps({"status": "fail", "failure_reason": "unavailable_subagent_tools", "error": "No selected tools are available", "subagent_id": subagent_id})

    # Build child system prompt
    child_prompt = build_cc_backend_system_prompt(
        workdir=child_workdir,
        filesystem_mode=filesystem_mode,
        model_name=model,
        wrapped_task_context="",
    )
    from .prompt_assembly import subagent_session_guidance
    child_prompt += subagent_session_guidance(can_delegate=delegate_depth + 1 < MAX_DEPTH)


    effective_max_iter = DEFAULT_MAX_ITERATIONS
    if isinstance(max_iterations, int) and max_iterations > 0:
        effective_max_iter = min(max_iterations, DEFAULT_MAX_ITERATIONS)

    child_budget = IterationBudget(effective_max_iter)

    child_task_id = f"delegate_{child_id}"
    child_metadata = dict(kwargs.get("delegate_metadata") or {})
    child_metadata.pop("forced_tool_call", None)
    child_metadata.pop("must_write_skills", None)
    blocked_resume = resume.get("blocked")
    if isinstance(blocked_resume, dict):
        from .chat_session import _forced_tool_call_for_resume
        resume_call = blocked_resume
        resume_context = blocked_resume.get("approval_context_messages") or []
        if isinstance(blocked_resume.get("delegate_resume"), dict):
            resume_call = {"tool": "delegate_task", "input": blocked_resume["delegate_resume"], "tool_use_id": "resume-child"}
            resume_context = blocked_resume.get("parent_approval_context_messages") or []
        child_metadata["forced_tool_call"] = _forced_tool_call_for_resume(resume_call, resume_context)

    try:
        result = execute_cc_backend_task(
            base_url=base_url,
            model=model,
            system_prompt=child_prompt,
            user_message=goal + ("\n\nCONTEXT:\n" + context if context else ""),
            workdir=child_workdir,
            artifact_workdir=artifact_workdir or child_workdir,
            filesystem_mode=filesystem_mode,
            guard_mode=guard_mode,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            current_session=current_session,
            source_platform=source_platform,
            source_chat_id=source_chat_id,
            delegate_depth=delegate_depth + 1,
            iteration_budget=child_budget,
            enabled_tools=child_schemas,
            parent_task_id=parent_task_id or child_task_id,
            progress_callback=_child_progress,
            subagent_id=subagent_id,
            profile=profile,
            api_format=api_format,
            request_headers=request_headers,
            user_input_provider=child_input_provider if callable(parent_input_provider) else None,
            web_tool_provider=kwargs.get("web_tool_provider"),
            session_search_provider=kwargs.get("session_search_provider"),
            memory_forget_provider=kwargs.get("memory_forget_provider"),
            data_root=kwargs.get("data_root"),
            checkpoint_config=kwargs.get("checkpoint_config"),
            approvals_enabled_provider=kwargs.get("approvals_enabled_provider"),
            task_id=str(kwargs.get("task_id") or parent_task_id),
            metadata=child_metadata,
        )
    except Exception as exc:
        reporter.finish(
            subagent_id=subagent_id,
            status=STATUS_ERROR,
            error=f"Subagent execution failed: {exc}",
        )
        return json.dumps(
            {"error": f"Subagent execution failed: {exc}", "subagent_id": subagent_id},
            ensure_ascii=False,
        )

    status = str(result.get("status") or "")
    cancelled = status == "cancelled" or cancel_requested(artifact_workdir, subagent_id)
    if result.get("failure_reason") == "approval_required":
        final_status = "waiting_approval"
    elif cancelled:
        final_status = STATUS_CANCELLED
    elif status in {"ok", "completed", "success"}:
        final_status = STATUS_OK
    else:
        final_status = STATUS_ERROR
    reporter.finish(
        subagent_id=subagent_id,
        status=final_status,
        summary=str(result.get("output_text") or ""),
        error=str(result.get("failure_reason") or result.get("error") or ""),
        usage=result.get("usage") if isinstance(result.get("usage"), dict) else None,
        tool_calls_count=result.get("tool_calls_count")
        if isinstance(result.get("tool_calls_count"), int)
        else None,
    )

    blocked_tools = []
    if result.get("failure_reason") == "approval_required":
        for blocked in result.get("approval_blocked_tools") or []:
            if not isinstance(blocked, dict):
                continue
            blocked_tools.append({**blocked, "agent_session_id": blocked.get("agent_session_id") or "agent-" + subagent_id,
                                  "delegate_resume": {"goal": goal, "context": context, "toolsets": toolsets,
                                      "max_iterations": max_iterations, "_resume": {"subagent_id": subagent_id,
                                          "child_id": child_id, "blocked": blocked}}})
    return json.dumps({
        "approval_blocked_tools": blocked_tools,
        "failure_reason": result.get("failure_reason"),
        "status": result.get("status"),
        "summary": result.get("output_text"),
        "tool_calls_count": result.get("tool_calls_count"),
        "usage": result.get("usage"),
        "subagent_id": subagent_id,
    }, ensure_ascii=False, indent=2)


# Anthropic-format schema
DELEGATE_TASK_SCHEMA: dict[str, Any] = {
    "name": "delegate_task",
    "description": (
        "Spawn a subagent to work on a delegated task in an isolated context. "
        "The subagent gets its own workspace, restricted tools, and iteration budget. "
        "Only the final summary is returned to you.\n\n"
        "WHEN TO USE:\n"
        "- Reasoning-heavy subtasks (debugging, research, code review)\n"
        "- Tasks that would flood your context with intermediate data\n\n"
        "IMPORTANT:\n"
        "- Subagents have NO memory of your conversation. Pass all relevant info via 'context'.\n"
        "- Subagents may delegate within the runtime depth limit.\n"
        "- Each subagent gets its own isolated workspace."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "What the subagent should accomplish. Be specific and "
                    "self-contained — the subagent knows nothing about your history."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Background information: file paths, error messages, constraints. "
                    "The more specific, the better the subagent performs."
                ),
            },
            "toolsets": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Tool names or toolset names to enable, e.g. ['Bash', 'Write'] or ['terminal', 'file']. "
                    "Default: inherits all available tools minus blocked ones."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    "Max tool-calling turns per subagent (default: 50). "
                    "Only set lower for simple tasks."
                ),
            },
        },
        "required": ["goal"],
    },
}


# Register
from .tool_registry import registry

registry.register(
    name="delegate_task",
    toolset="delegation",
    schema=DELEGATE_TASK_SCHEMA,
    handler=delegate_task,
    check_fn=check_delegate_requirements,
    description="Spawn a subagent to work on a delegated task in an isolated context.",
)
