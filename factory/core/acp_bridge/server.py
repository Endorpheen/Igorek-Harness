"""ACP agent server — exposes factory cc-backend via Agent Client Protocol.

Runs as stdio JSON-RPC server, compatible with VS Code / Zed / JetBrains.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import acp
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    AvailableCommand,
    AvailableCommandsUpdate,
    ClientCapabilities,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionForkCapabilities,
    SessionInfo,
    SessionListCapabilities,
    TextContentBlock,
    UnstructuredCommandInput,
)

from .events import make_message_cb, make_thinking_cb, make_tool_progress_cb
from .session import SessionManager, SessionState

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="acp-agent")

try:
    FACTORY_VERSION = version("igorek-agent-factory")
except PackageNotFoundError:
    FACTORY_VERSION = "dev"


def _extract_text(prompt: list) -> str:
    """Extract plain text from ACP content blocks."""
    parts = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(str(block.text))
    return "\n".join(parts)


class FactoryACPAgent(acp.Agent):
    """ACP Agent implementation wrapping factory cc_backend_executor."""

    _SLASH_COMMANDS = {
        "help": "List available commands",
        "tools": "List available tools",
        "reset": "Clear conversation history",
        "version": "Show factory version",
    }

    _ADVERTISED_COMMANDS = (
        {"name": "help", "description": "List available commands"},
        {"name": "tools", "description": "List available tools"},
        {"name": "reset", "description": "Clear conversation history"},
        {"name": "version", "description": "Show factory version"},
    )

    def __init__(self, session_manager: SessionManager | None = None):
        super().__init__()
        self.session_manager = session_manager or SessionManager()
        self._conn: Any = None

    def on_connect(self, conn: Any) -> None:
        self._conn = conn
        logger.info("ACP client connected")

    async def initialize(
        self,
        protocol_version: int | None = None,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        resolved_protocol_version = (
            protocol_version
            if isinstance(protocol_version, int)
            else acp.PROTOCOL_VERSION
        )

        client_name = client_info.name if client_info else "unknown"
        logger.info(
            "Initialize from %s (protocol v%s)", client_name, resolved_protocol_version
        )

        return InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_info=Implementation(name="factory-agent", version=FACTORY_VERSION),
            agent_capabilities=AgentCapabilities(
                session_capabilities=SessionCapabilities(
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                ),
            ),
            auth_methods=None,
        )

    async def authenticate(
        self, method_id: str, **kwargs: Any
    ) -> AuthenticateResponse | None:
        return None

    async def new_session(
        self, cwd: str, mcp_servers: list | None = None, **kwargs: Any
    ) -> NewSessionResponse:
        state = self.session_manager.create_session(cwd=cwd)
        logger.info("New session %s (cwd=%s)", state.session_id, cwd)
        self._schedule_available_commands_update(state.session_id)
        return NewSessionResponse(session_id=state.session_id)

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: list | None = None, **kwargs: Any
    ) -> ResumeSessionResponse | None:
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.warning("load_session: session %s not found", session_id)
            return None
        state.cwd = cwd
        logger.info("Loaded session %s", session_id)
        self._schedule_available_commands_update(session_id)
        return ResumeSessionResponse()

    async def resume_session(
        self, cwd: str, session_id: str, mcp_servers: list | None = None, **kwargs: Any
    ) -> ResumeSessionResponse:
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.warning(
                "resume_session: session %s not found, creating new", session_id
            )
            state = self.session_manager.create_session(cwd=cwd)
        else:
            state.cwd = cwd
        logger.info("Resumed session %s", state.session_id)
        self._schedule_available_commands_update(state.session_id)
        return ResumeSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        state = self.session_manager.get_session(session_id)
        if state and state.cancel_event:
            state.cancel_event.set()
            logger.info("Cancelled session %s", session_id)

    async def fork_session(
        self, cwd: str, session_id: str, mcp_servers: list | None = None, **kwargs: Any
    ) -> ForkSessionResponse:
        state = self.session_manager.fork_session(session_id, cwd=cwd)
        new_id = state.session_id if state else ""
        logger.info("Forked session %s -> %s", session_id, new_id)
        if new_id:
            self._schedule_available_commands_update(new_id)
        return ForkSessionResponse(session_id=new_id)

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        infos = self.session_manager.list_sessions()
        sessions = [
            SessionInfo(session_id=s["session_id"], cwd=s["cwd"]) for s in infos
        ]
        return ListSessionsResponse(sessions=sessions)

    async def prompt(
        self,
        prompt: list,
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        """Run factory cc-backend on the user's prompt and stream events back to the editor."""
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.error("prompt: session %s not found", session_id)
            return PromptResponse(stop_reason="refusal")

        user_text = _extract_text(prompt).strip()
        if not user_text:
            return PromptResponse(stop_reason="end_turn")

        if user_text.startswith("/"):
            response_text = self._handle_slash_command(user_text, state)
            if response_text is not None:
                if self._conn:
                    update = acp.update_agent_message_text(response_text)
                    await self._conn.session_update(session_id, update)
                return PromptResponse(stop_reason="end_turn")

        logger.info("Prompt on session %s: %s", session_id, user_text[:100])

        conn = self._conn
        loop = asyncio.get_running_loop()

        if state.cancel_event:
            state.cancel_event.clear()

        tool_call_ids: dict[str, deque[str]] = defaultdict(deque)

        tool_progress_cb = None
        thinking_cb = None
        message_cb = None

        if conn:
            tool_progress_cb = make_tool_progress_cb(
                conn, session_id, loop, tool_call_ids
            )
            thinking_cb = make_thinking_cb(conn, session_id, loop)
            message_cb = make_message_cb(conn, session_id, loop)

        def _run_agent() -> dict:
            from .cc_backend_executor import execute_cc_backend_task
            from .prompt_assembly import build_cc_backend_system_prompt

            system_prompt = build_cc_backend_system_prompt(
                workdir=state.cwd,
                filesystem_mode="workspace",
                model_name="",
                wrapped_task_context="",
            )

            try:
                result = execute_cc_backend_task(
                    base_url="http://127.0.0.1:8001",
                    model="",
                    system_prompt=system_prompt,
                    user_message=user_text,
                    workdir=state.cwd,
                    filesystem_mode="workspace",
                    max_tokens=4000,
                    timeout_s=600,
                    current_session=session_id,
                    source_platform="acp",
                    source_chat_id=session_id,
                    delegate_depth=0,
                    enabled_tools=None,
                    parent_task_id=session_id,
                    progress_callback=tool_progress_cb,
                )
                return {
                    "final_response": result.get("output_text", ""),
                    "messages": state.history,
                    "usage": result.get("usage"),
                }
            except Exception as e:
                logger.exception("Agent error in session %s", session_id)
                return {"final_response": f"Error: {e}", "messages": state.history}

        try:
            result = await loop.run_in_executor(_executor, _run_agent)
        except Exception:
            logger.exception("Executor error for session %s", session_id)
            return PromptResponse(stop_reason="end_turn")

        if result.get("messages"):
            state.history = result["messages"]

        final_response = result.get("final_response", "")
        if final_response and conn:
            update = acp.update_agent_message_text(final_response)
            await conn.session_update(session_id, update)

        stop_reason = (
            "cancelled"
            if state.cancel_event and state.cancel_event.is_set()
            else "end_turn"
        )
        return PromptResponse(stop_reason=stop_reason)

    @classmethod
    def _available_commands(cls) -> list[AvailableCommand]:
        commands = []
        for spec in cls._ADVERTISED_COMMANDS:
            input_hint = spec.get("input_hint")
            commands.append(
                AvailableCommand(
                    name=spec["name"],
                    description=spec["description"],
                    input=UnstructuredCommandInput(hint=input_hint)
                    if input_hint
                    else None,
                )
            )
        return commands

    async def _send_available_commands_update(self, session_id: str) -> None:
        if not self._conn:
            return
        try:
            await self._conn.session_update(
                session_id=session_id,
                update=AvailableCommandsUpdate(
                    sessionUpdate="available_commands_update",
                    availableCommands=self._available_commands(),
                ),
            )
        except Exception:
            logger.warning(
                "Failed to advertise ACP slash commands for session %s",
                session_id,
                exc_info=True,
            )

    def _schedule_available_commands_update(self, session_id: str) -> None:
        if not self._conn:
            return
        loop = asyncio.get_running_loop()
        loop.call_soon(
            asyncio.create_task, self._send_available_commands_update(session_id)
        )

    def _handle_slash_command(self, text: str, state: SessionState) -> str | None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        handler = {
            "help": self._cmd_help,
            "tools": self._cmd_tools,
            "reset": self._cmd_reset,
            "version": self._cmd_version,
        }.get(cmd)

        if handler is None:
            return None

        try:
            return handler(args, state)
        except Exception as e:
            logger.error("Slash command /%s error: %s", cmd, e, exc_info=True)
            return f"Error executing /{cmd}: {e}"

    def _cmd_help(self, args: str, state: SessionState) -> str:
        lines = ["Available commands:", ""]
        for cmd, desc in self._SLASH_COMMANDS.items():
            lines.append(f"  /{cmd:10s}  {desc}")
        lines.append("")
        lines.append("Unrecognized /commands are sent to the model as normal messages.")
        return "\n".join(lines)

    def _cmd_tools(self, args: str, state: SessionState) -> str:
        from .tool_registry import registry

        tools = registry.list_tools()
        lines = [f"Available tools ({len(tools)}):", ""]
        for t in tools[:20]:
            lines.append(f"  {t}")
        if len(tools) > 20:
            lines.append(f"  ... and {len(tools) - 20} more")
        return "\n".join(lines)

    def _cmd_reset(self, args: str, state: SessionState) -> str:
        state.history.clear()
        return "Conversation history cleared."

    def _cmd_version(self, args: str, state: SessionState) -> str:
        return f"factory-agent v{FACTORY_VERSION}"


async def _run_server() -> None:
    """Run the ACP server with stdout transport."""
    agent = FactoryACPAgent()
    try:
        await asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
    except Exception:
        logger.exception("ACP agent crashed")
        sys.exit(1)


def main() -> None:
    """Entry point: configure logging and run ACP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("Starting factory ACP bridge")
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
