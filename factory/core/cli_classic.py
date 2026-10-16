"""Compact terminal-owned startup surface, following Codex's inline presentation."""
from pathlib import Path
from rich.text import Text


def header(console, *, profile, model, version, cwd):
    width = max(16, console.width)
    title = Text.assemble(("Игорёк", "bold #bbed74"), ("  " + version, "dim"))
    console.print(title)
    for value in [profile + " · " + model, str(Path(cwd))]:
        line = Text(value, style="dim")
        line.truncate(width, overflow="ellipsis")
        console.print(line)
    console.print(Text("─" * min(width, 76), style="#34463b"))
    console.print(Text("/help · /profile · Ctrl+C — выход", style="dim"))
    console.print()
