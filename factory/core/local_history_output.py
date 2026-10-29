"""Keep unquoted internal history records out of local model answers."""
from __future__ import annotations

from .model_backend_capabilities import ModelBackendCapabilities


_MARKERS = (
    "[Historical tool evidence; already executed]",
    "[Historical tool evidence]",
    "[Historical reasoning evidence]",
)


def local_history_guard_enabled(base_url: str, api_format: str = "openai", *,
                                capabilities: ModelBackendCapabilities | None = None, model: str = "") -> bool:
    return (api_format in {"openai", "anthropic"}
            and isinstance(capabilities, ModelBackendCapabilities)
            and capabilities.backend == "llama_cpp"
            and capabilities.matches(base_url, model))


class HistoricalToolEchoFilter:
    """Hold ambiguous line prefixes before publishing visible text deltas."""

    def __init__(self):
        self.blocked = False
        self._pending = ""
        self._plain_line = False
        self._fence = None

    def _decision(self, *, final=False):
        line = self._pending
        text = line.lstrip(" ")
        if len(line) - len(text) >= 4 or text.startswith("\t"):
            return "emit"
        if not text:
            return "emit" if final else "hold"
        if self._fence:
            char, minimum = self._fence
            run = len(text) - len(text.lstrip(char))
            if run:
                tail = text[run:]
                if not tail and not final:
                    return "hold"
                if run >= minimum and not tail.strip():
                    if "\n" in tail or final:
                        self._fence = None
                        return "emit"
                    return "hold"
            return "emit"
        if text[0] in "`~":
            char = text[0]
            run = len(text) - len(text.lstrip(char))
            if run == len(text) and not final:
                return "hold"
            if run >= 3:
                self._fence = (char, run)
            return "emit"
        for marker in _MARKERS:
            if marker.startswith(text):
                if final:
                    return "block" if len(text) >= len("[Historical tool") else "emit"
                return "hold"
            if text.startswith(marker):
                tail = text[len(marker):]
                if not tail.strip():
                    return "block" if "\n" in tail or final else "hold"
        return "emit"

    def feed(self, text: str) -> str:
        output = []
        for char in text:
            if self.blocked:
                break
            if self._plain_line:
                output.append(char)
                if char == "\n":
                    self._plain_line = False
                continue
            self._pending += char
            decision = self._decision()
            if decision == "block":
                self.blocked = True
                self._pending = ""
            elif decision == "emit":
                output.append(self._pending)
                self._plain_line = not self._pending.endswith("\n")
                self._pending = ""
        return "".join(output)

    def finish(self) -> str:
        if self.blocked:
            return ""
        if self._pending and self._decision(final=True) == "block":
            self.blocked = True
            self._pending = ""
            return ""
        pending, self._pending = self._pending, ""
        return pending


def contains_historical_tool_echo(text: str) -> bool:
    guard = HistoricalToolEchoFilter()
    guard.feed(text)
    guard.finish()
    return guard.blocked
