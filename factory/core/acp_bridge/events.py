"""Callback factories for bridging cc-backend events to ACP notifications.

Each factory returns a callable that sends ACP session updates to the editor
via conn.session_update().
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from typing import Any, Callable, Deque

import acp

logger = logging.getLogger(__name__)


def _send_update(
    conn: acp.Client, session_id: str, loop: asyncio.AbstractEventLoop, update: Any
) -> None:
    """Fire-and-forget an ACP session update from a worker thread."""
    try:
        future = asyncio.run_coroutine_threadsafe(
            conn.session_update(session_id, update), loop
        )
        future.result(timeout=5)
    except Exception:
        logger.debug("Failed to send ACP update", exc_info=True)


def make_message_cb(
    conn: acp.Client, session_id: str, loop: asyncio.AbstractEventLoop
) -> Callable:
    """Create a callback that streams agent response text to the editor."""

    def _message(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_message_text(text)
        _send_update(conn, session_id, loop, update)

    return _message


def make_thinking_cb(
    conn: acp.Client, session_id: str, loop: asyncio.AbstractEventLoop
) -> Callable:
    """Create a callback that streams agent thinking/reasoning to the editor."""

    def _thinking(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_thought_text(text)
        _send_update(conn, session_id, loop, update)

    return _thinking


def make_tool_progress_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: dict[str, Deque[str]],
) -> Callable:
    """Create a tool_progress_callback for cc-backend.

    Sends ToolCallStart for tool.started events and ToolCallComplete for tool.completed.
    """

    def _tool_progress(
        event_type: str,
        name: str = None,
        preview: str = None,
        args: Any = None,
        result: Any = None,
        **kwargs,
    ) -> None:
        if event_type == "tool.started":
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": args}
            if not isinstance(args, dict):
                args = {}

            tc_id = f"tc-{uuid.uuid4().hex[:12]}"
            queue = tool_call_ids.get(name)
            if queue is None:
                queue = deque()
                tool_call_ids[name] = queue
            queue.append(tc_id)

            update = _build_tool_start(tc_id, name, args)
            _send_update(conn, session_id, loop, update)

        elif event_type == "tool.completed":
            if not name:
                return
            queue = tool_call_ids.get(name)
            if not queue:
                return
            tc_id = queue.popleft()
            result_text = str(result) if result is not None else ""
            update = _build_tool_complete(tc_id, name, result_text)
            _send_update(conn, session_id, loop, update)
            if not queue:
                tool_call_ids.pop(name, None)

    return _tool_progress


def _make_tool_call_id() -> str:
    """Generate a unique tool call ID."""
    import uuid

    return f"tc-{uuid.uuid4().hex[:12]}"


def _build_tool_start(
    tool_call_id: str, tool_name: str, arguments: dict[str, Any]
) -> Any:
    """Build a ToolCallStart event."""
    title = _build_tool_title(tool_name, arguments)
    content = [acp.tool_content(acp.text_block(str(arguments)))]
    return acp.start_tool_call(
        tool_call_id, title, kind="other", content=content, raw_input=arguments
    )


def _build_tool_complete(tool_call_id: str, tool_name: str, result: str) -> Any:
    """Build a ToolCallComplete event."""
    display_result = result
    if len(display_result) > 5000:
        display_result = (
            display_result[:4900] + f"\n... ({len(result)} chars total, truncated)"
        )
    content = [acp.tool_content(acp.text_block(display_result))]
    return acp.update_tool_call(
        tool_call_id,
        kind="other",
        status="completed",
        content=content,
        raw_output=result,
    )


def _build_tool_title(tool_name: str, args: dict[str, Any]) -> str:
    """Build a human-readable title for a tool call."""
    if tool_name == "Bash":
        cmd = args.get("command", "")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"bash: {cmd}"
    if tool_name == "Read":
        return f"read: {args.get('file_path', '?')}"
    if tool_name == "Write":
        return f"write: {args.get('file_path', '?')}"
    if tool_name == "Edit":
        return f"edit: {args.get('file_path', '?')}"
    if tool_name == "delegate_task":
        goal = args.get("goal", "")
        if len(goal) > 60:
            goal = goal[:57] + "..."
        return f"delegate: {goal}" if goal else "delegate task"
    return f"{tool_name}"
