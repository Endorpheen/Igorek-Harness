"""Slash commands the TUI understands (port of codex-rs/tui/src/slash_command.rs).

The Python port uses a plain enum with explicit ``command`` strings
plus a manual ``from_str`` resolver that honors the original
``#[strum(serialize = ...)`` and ``#[strum(to_string = ...)`` annotations.
"""
from __future__ import annotations

import sys
from enum import Enum
from typing import List, Tuple


# Hand-transcribed from the Rust source. The keys are *aliases* (extra
# strings accepted by ``from_str``); the canonical command string is the
# value of ``SlashCommand.command()``.
_ALIASES = {
    "setup-default-sandbox": "ElevateSandbox",
    "sandbox-add-read-dir": "SandboxReadRoot",
    "approve": "AutoReview",
    "pets": "Pets",
    "pet": "Pets",
    "stop": "Stop",
    "clean": "Stop",
    "subagents": "MultiAgents",
    "debug-m-drop": "MemoryDrop",
    "debug-m-update": "MemoryUpdate",
}


class SlashCommand(Enum):
    # DO NOT ALPHA-SORT! Enum order is presentation order in the popup, so
    # more frequently used commands should be listed first.
    Model = "model"
    Ide = "ide"
    Permissions = "permissions"
    Keymap = "keymap"
    Vim = "vim"
    ElevateSandbox = "elevate-sandbox"
    SandboxReadRoot = "sandbox-add-read-dir"
    Experimental = "experimental"
    AutoReview = "approve"
    Memories = "memories"
    Skills = "skills"
    Hooks = "hooks"
    Review = "review"
    Rename = "rename"
    New = "new"
    Archive = "archive"
    Resume = "resume"
    Fork = "fork"
    Init = "init"
    Compact = "compact"
    Plan = "plan"
    Goal = "goal"
    Agent = "agent"
    Side = "side"
    Btw = "btw"
    Copy = "copy"
    Raw = "raw"
    Diff = "diff"
    Mention = "mention"
    Status = "status"
    DebugConfig = "debug-config"
    Title = "title"
    Statusline = "statusline"
    Theme = "theme"
    Pets = "pets"
    Mcp = "mcp"
    Apps = "apps"
    Plugins = "plugins"
    Logout = "logout"
    Quit = "quit"
    Exit = "exit"
    Feedback = "feedback"
    Rollout = "rollout"
    Ps = "ps"
    Stop = "stop"
    Clear = "clear"
    Personality = "personality"
    Realtime = "realtime"
    Settings = "settings"
    TestApproval = "test-approval"
    MultiAgents = "subagents"
    MemoryDrop = "debug-m-drop"
    MemoryUpdate = "debug-m-update"

    def command(self) -> str:
        """Return the canonical command string (without the leading ``/``)."""
        return self.value

    def description(self) -> str:
        """User-visible description shown in the popup (port one-to-one)."""
        return _DESCRIPTIONS[self]

    def supports_inline_args(self) -> bool:
        return self in _INLINE_ARGS

    def available_in_side_conversation(self) -> bool:
        return self in _SIDE_AVAILABLE

    def available_during_task(self) -> bool:
        return self in _DURING_TASK

    def is_visible(self) -> bool:
        """Match the Rust ``cfg!()`` predicates used by the source.

        ``SandboxReadRoot`` is Windows-only in codex; ``Copy`` is hidden
        on Android (irrelevant on the Python port, we keep the
        conditional explicit). ``Rollout`` and ``TestApproval`` are
        ``debug_assertions``-gated in Rust - we treat the Python port as
        non-debug (equivalent to a release build) by default, but the
        caller can monkey-patch this method.
        """
        if self == SlashCommand.SandboxReadRoot:
            return sys.platform == "win32"
        if self == SlashCommand.Copy:
            return sys.platform != "android"
        if self in (SlashCommand.Rollout, SlashCommand.TestApproval):
            return getattr(self, "_is_debug", False)
        return True

    @staticmethod
    def from_str(name: str) -> "SlashCommand":
        """Port of ``strum::EnumString`` for the original ``strum(serialize_all = "kebab-case")``."""
        alias_target = _ALIASES.get(name)
        if alias_target is not None:
            return SlashCommand[alias_target]
        for cmd in SlashCommand:
            if cmd.value == name:
                return cmd
        raise ValueError(f"unknown slash command: {name!r}")


_DESCRIPTIONS = {
    SlashCommand.Feedback: "send logs to maintainers",
    SlashCommand.New: "start a new chat during a conversation",
    SlashCommand.Init: "create an AGENTS.md file with instructions for Codex",
    SlashCommand.Compact: "summarize conversation to prevent hitting the context limit",
    SlashCommand.Review: "review my current changes and find issues",
    SlashCommand.Rename: "rename the current thread",
    SlashCommand.Resume: "resume a saved chat",
    SlashCommand.Archive: "archive this session and exit",
    SlashCommand.Clear: "clear the terminal and start a new chat",
    SlashCommand.Fork: "fork the current chat",
    SlashCommand.Quit: "exit Codex",
    SlashCommand.Exit: "exit Codex",
    SlashCommand.Copy: "copy last response as markdown",
    SlashCommand.Raw: "toggle raw scrollback mode for copy-friendly terminal selection",
    SlashCommand.Diff: "show git diff (including untracked files)",
    SlashCommand.Mention: "mention a file",
    SlashCommand.Skills: "use skills to improve how Codex performs specific tasks",
    SlashCommand.Hooks: "view and manage lifecycle hooks",
    SlashCommand.Status: "show current session configuration and token usage",
    SlashCommand.DebugConfig: "show config layers and requirement sources for debugging",
    SlashCommand.Title: "configure which items appear in the terminal title",
    SlashCommand.Statusline: "configure which items appear in the status line",
    SlashCommand.Theme: "choose a syntax highlighting theme",
    SlashCommand.Pets: "choose or hide the terminal pet",
    SlashCommand.Ps: "list background terminals",
    SlashCommand.Stop: "stop all background terminals",
    SlashCommand.MemoryDrop: "DO NOT USE",
    SlashCommand.MemoryUpdate: "DO NOT USE",
    SlashCommand.Model: "choose what model and reasoning effort to use",
    SlashCommand.Ide: "include current selection, open files, and other context from your IDE",
    SlashCommand.Personality: "choose a communication style for Codex",
    SlashCommand.Realtime: "toggle realtime voice mode (experimental)",
    SlashCommand.Settings: "configure realtime microphone/speaker",
    SlashCommand.Plan: "switch to Plan mode",
    SlashCommand.Goal: "set or view the goal for a long-running task",
    SlashCommand.Agent: "switch the active agent thread",
    SlashCommand.MultiAgents: "switch the active agent thread",
    SlashCommand.Side: "start a side conversation in an ephemeral fork",
    SlashCommand.Btw: "start a side conversation in an ephemeral fork",
    SlashCommand.Permissions: "choose what Codex is allowed to do",
    SlashCommand.Keymap: "remap TUI shortcuts",
    SlashCommand.Vim: "toggle Vim mode for the composer",
    SlashCommand.ElevateSandbox: "set up elevated agent sandbox",
    SlashCommand.SandboxReadRoot: "let sandbox read a directory: /sandbox-add-read-dir <absolute_path>",
    SlashCommand.Experimental: "toggle experimental features",
    SlashCommand.AutoReview: "approve one retry of a recent auto-review denial",
    SlashCommand.Memories: "configure memory use and generation",
    SlashCommand.Mcp: "list configured MCP tools; use /mcp verbose for details",
    SlashCommand.Apps: "manage apps",
    SlashCommand.Plugins: "browse plugins",
    SlashCommand.Logout: "log out of Codex",
    SlashCommand.Rollout: "print the rollout file path",
    SlashCommand.TestApproval: "test approval request",
}


_INLINE_ARGS = {
    SlashCommand.Review,
    SlashCommand.Rename,
    SlashCommand.Plan,
    SlashCommand.Goal,
    SlashCommand.Ide,
    SlashCommand.Keymap,
    SlashCommand.Mcp,
    SlashCommand.Raw,
    SlashCommand.Pets,
    SlashCommand.Side,
    SlashCommand.Btw,
    SlashCommand.Resume,
    SlashCommand.SandboxReadRoot,
}


_SIDE_AVAILABLE = {
    SlashCommand.Copy,
    SlashCommand.Raw,
    SlashCommand.Diff,
    SlashCommand.Mention,
    SlashCommand.Status,
    SlashCommand.Ide,
}


_DURING_TASK = {
    SlashCommand.Diff,
    SlashCommand.Copy,
    SlashCommand.Raw,
    SlashCommand.Rename,
    SlashCommand.Mention,
    SlashCommand.Skills,
    SlashCommand.Hooks,
    SlashCommand.Status,
    SlashCommand.DebugConfig,
    SlashCommand.Ps,
    SlashCommand.Stop,
    SlashCommand.Goal,
    SlashCommand.Mcp,
    SlashCommand.Apps,
    SlashCommand.Plugins,
    SlashCommand.Title,
    SlashCommand.Statusline,
    SlashCommand.AutoReview,
    SlashCommand.Feedback,
    SlashCommand.Ide,
    SlashCommand.Quit,
    SlashCommand.Exit,
    SlashCommand.Side,
    SlashCommand.Btw,
    SlashCommand.Rollout,
    SlashCommand.TestApproval,
    SlashCommand.Realtime,
    SlashCommand.Settings,
    SlashCommand.Agent,
    SlashCommand.MultiAgents,
}


def built_in_slash_commands() -> List[Tuple[str, SlashCommand]]:
    """Return all visible built-in slash commands paired with their command string."""
    return [(c.command(), c) for c in SlashCommand if c.is_visible()]


__all__ = [
    "SlashCommand",
    "built_in_slash_commands",
]
