"""Slash commands — port of Codex bottom_pane/slash_commands.rs."""
from __future__ import annotations

BUILTIN_COMMANDS: list[tuple[str, str, str]] = [
    ("/help", "Show help", "📋 Display all commands and key bindings"),
    ("/exit", "Exit", "🚪 Exit the session"),
    ("/clear", "Clear history", "🧹 Clear session history"),
    ("/history", "Show history", "📜 Last 20 messages"),
    ("/resume", "Resume session", "📂 Choose a previous session"),
    ("/fork", "Fork session", "📋 Copy a previous session into a new one"),
    ("/status", "Session status", "📊 Show session info"),
    ("/model", "Change model", "🤖 Show or change model"),
    ("/profile", "Change profile", "👤 Show or change profile"),
    ("/provider", "Change provider", "🔌 Show or change provider"),
    ("/models", "List models", "📋 Recommended models and session picker"),
    ("/statusline", "Status line", "📟 Show status line settings"),
    ("/skills", "Skills", "🛠️ Manage local skills"),
    ("/remember", "Remember fact", "🧠 Explicitly save a durable memory fact"),
    ("/smarthelp", "Smart help", "💡 Quick answer via OpenRouter"),
    ("/debug", "Debug mode", "🐛 Toggle debug logging"),
    ("/notifications", "Notifications", "🔔 Toggle desktop notifications"),
    ("/title", "Terminal title", "📝 Set terminal title"),
    ("/theme", "Syntax theme", "🎨 Select markdown and diff theme"),
    ("/plan", "Plan mode", "📐 Enter plan mode"),
]
