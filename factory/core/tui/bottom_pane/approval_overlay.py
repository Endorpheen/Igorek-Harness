"""Approval overlay — full port of Codex bottom_pane/approval_overlay.rs.

Structural invariant: the approval body never renders the actual diff/payload
content. The diff is rendered separately as a `DiffCell` in the committed
history (see `TraceEventReducer._apply_tool_request_batch`). The approval block
only shows: title + optional reason + tool/header (e.g. `Edit: /path`) +
3 action choices.
"""
from __future__ import annotations
import difflib
import os
from typing import Optional, Callable
from ..markdown_render import _format_inline
from .scroll_state import ScrollState

class ApprovalRequest:
    def __init__(self, tool_name: str, target: str, signature: str = "",
                 source: str = "", tool_input: Optional[dict] = None,
                 reasoning: str = ""):
        self.tool_name = tool_name
        self.target = target
        self.signature = signature
        self.source = source
        self.tool_input = tool_input or {}
        self.reasoning = reasoning

class ApprovalOverlay:
    """Codex approval overlay: interactive dialog for tool authorization."""

    MAX_REASON_LINES = 4

    def __init__(self):
        self.requests: list[ApprovalRequest] = []
        self._selected: int = 0
        self._selected_action: int = 0
        self.visible: bool = False
        self.session_allowed: set[str] = set()
        self._width: int = 80
        self._on_allow: Optional[Callable[[str], None]] = None
        self._on_reject: Optional[Callable[[], None]] = None
        self._on_allow_session: Optional[Callable[[str], None]] = None

    def set_width(self, w: int): self._width = w

    def set_on_allow(self, cb: Callable[[str], None]): self._on_allow = cb
    def set_on_reject(self, cb: Callable[[], None]): self._on_reject = cb
    def set_on_allow_session(self, cb: Callable[[str], None]): self._on_allow_session = cb

    def set_requests(self, requests: list[ApprovalRequest]):
        self.requests = requests
        self._selected = 0
        self._selected_action = 0
        self.visible = bool(requests)

    def current(self) -> Optional[ApprovalRequest]:
        if 0 <= self._selected < len(self.requests):
            return self.requests[self._selected]
        return None

    def allow(self) -> Optional[str]:
        req = self.current()
        if req:
            sig = req.signature or f"{req.tool_name}:{req.target}"
            if self._on_allow: self._on_allow(sig)
            return sig
        return None

    def reject(self):
        if self._on_reject: self._on_reject()

    def allow_session(self):
        req = self.current()
        if req:
            self.session_allowed.add(req.tool_name)
            if self._on_allow_session:
                sig = req.signature or f"{req.tool_name}:{req.target}"
                self._on_allow_session(sig)

    def is_session_allowed(self, tool_name: str) -> bool:
        return tool_name in self.session_allowed

    def move_selection(self, delta: int):
        if self.requests:
            self._selected = (self._selected + delta) % len(self.requests)

    def move_action_selection(self, delta: int):
        self._selected_action = (self._selected_action + delta) % 3

    def set_action_selection(self, index: int):
        self._selected_action = max(0, min(index, 2))

    def selected_action(self) -> str:
        return ("allow", "reject", "allow_session")[self._selected_action]

    def desired_height(self) -> int:
        return min(14, len(self.render_lines()))

    def render_lines(self) -> list[str]:
        if not self.visible or not self.requests:
            return []

        req = self.current()
        if req is None:
            return []

        surface_width = self._surface_width()
        content_width = self._surface_content_width(surface_width)
        body = []
        title = self._title_for(req)
        body.append("")
        body.append(f"  \033[1m{title}\033[0m")
        body.append("")
        if req.reasoning:
            body.extend(self._wrapped_reason_lines(req.reasoning, content_width))
            body.append("")
        body.extend(self._payload_lines(req, content_width))
        body.append("")
        # Wrap action labels so a long label like "Yes, and don't ask
        # again for these files (s)" never overflows the frame on
        # narrow terminals. Each wrapped chunk stays in the same body
        # row group but gets the action prefix on the first chunk and
        # continuation indent on the rest.
        action_width = max(20, content_width)
        for idx, label in enumerate(self._action_labels(req)):
            full_text = f"{idx + 1}. {label}"
            import textwrap as _tw
            chunks = _tw.wrap(
                full_text, width=action_width,
                replace_whitespace=True, drop_whitespace=True,
                break_long_words=True, break_on_hyphens=False,
            ) or [full_text]
            sel = idx == self._selected_action
            for cidx, chunk in enumerate(chunks):
                pmark = "\033[1;36m›\033[0m" if (sel and cidx == 0) else " "
                cont  = " " * len(f"{idx + 1}. ") if cidx > 0 else ""
                if sel and cidx == 0:
                    body.append(f"{pmark} \033[1m{chunk}\033[0m")
                elif idx == 1 and cidx == 0:
                    body.append(f"{pmark} \033[2m{chunk}\033[0m")
                else:
                    body.append(f"{pmark} {cont}{chunk}")
        body.append("")
        body.append("  \033[2mPress enter to confirm or esc to cancel\033[0m")
        return self._surface_lines(body, surface_width)

    def _surface_width(self) -> int:
        return max(28, min(self._width, 120))

    def _surface_content_width(self, surface_width: int) -> int:
        return max(20, surface_width - 4)

    def _surface_lines(self, body: list[str], surface_width: int) -> list[str]:
        bg = self._surface_bg_ansi()
        lines = []
        for line in body:
            line = self._clip_ansi_line(line, surface_width)
            plain_len = len(self._strip_ansi(line))
            pad = max(0, surface_width - plain_len)
            lines.append(f"{bg}{self._preserve_surface_bg(line, bg)}{' ' * pad}\033[0m")
        return lines

    def _surface_bg_ansi(self) -> str:
        r, g, b = self._surface_bg_rgb()
        return f"\033[48;2;{r};{g};{b}m"

    def _surface_bg_rgb(self) -> tuple[int, int, int]:
        bg = self._terminal_bg_rgb()
        light = self._is_light(bg)
        top = (0, 0, 0) if light else (255, 255, 255)
        alpha = 0.04 if light else 0.12
        return self._blend(top, bg, alpha)

    def _terminal_bg_rgb(self) -> tuple[int, int, int]:
        explicit = os.environ.get("IGOREK_TERMINAL_BG", "").strip()
        parsed = self._parse_rgb(explicit)
        if parsed is not None:
            return parsed
        colorfgbg = os.environ.get("COLORFGBG", "").strip()
        if colorfgbg:
            bg_token = colorfgbg.split(";")[-1]
            try:
                return self._ansi_color_rgb(int(bg_token))
            except (TypeError, ValueError):
                pass
        return (40, 44, 52)

    def _parse_rgb(self, value: str) -> Optional[tuple[int, int, int]]:
        if not value:
            return None
        if value.startswith("#") and len(value) == 7:
            try:
                return (
                    int(value[1:3], 16),
                    int(value[3:5], 16),
                    int(value[5:7], 16),
                )
            except ValueError:
                return None
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
        if len(parts) != 3:
            return None
        try:
            rgb = tuple(max(0, min(255, int(part))) for part in parts)
        except ValueError:
            return None
        return rgb  # type: ignore[return-value]

    def _ansi_color_rgb(self, idx: int) -> tuple[int, int, int]:
        palette = [
            (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
            (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
            (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
        ]
        return palette[idx] if 0 <= idx < len(palette) else (40, 44, 52)

    def _is_light(self, rgb: tuple[int, int, int]) -> bool:
        r, g, b = rgb
        return (0.299 * r + 0.587 * g + 0.114 * b) > 128.0

    def _blend(
        self,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int],
        alpha: float,
    ) -> tuple[int, int, int]:
        return (
            int(fg[0] * alpha + bg[0] * (1.0 - alpha)),
            int(fg[1] * alpha + bg[1] * (1.0 - alpha)),
            int(fg[2] * alpha + bg[2] * (1.0 - alpha)),
        )

    def _preserve_surface_bg(self, line: str, bg: str) -> str:
        return line.replace("\033[0m", f"\033[0m{bg}")

    def _strip_ansi(self, text: str) -> str:
        import re
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)

    def _clip_ansi_line(self, text: str, width: int) -> str:
        if len(self._strip_ansi(text)) <= width:
            return text

        limit = max(0, width - 1)
        out: list[str] = []
        plain_count = 0
        i = 0
        while i < len(text) and plain_count < limit:
            if text[i] == "\033":
                end = i + 1
                while end < len(text) and not text[end].isalpha():
                    end += 1
                if end < len(text):
                    end += 1
                out.append(text[i:end])
                i = end
                continue
            out.append(text[i])
            plain_count += 1
            i += 1
        out.append("…")
        if "\033[" in text:
            out.append("\033[0m")
        return "".join(out)

    def _title_for(self, req: ApprovalRequest) -> str:
        name = req.tool_name.lower()
        if name in {"bash", "exec_command"}:
            return "Would you like to run the following command?"
        if name in {"write", "edit", "notebookedit"}:
            return "Would you like to make the following edits?"
        return f"Would you like to allow {req.tool_name}?"

    def _payload_lines(self, req: ApprovalRequest, width: int) -> list[str]:
        name = req.tool_name.lower()
        target = req.target or req.tool_input.get("command") or req.tool_input.get("file_path") or req.tool_name
        if name in {"bash", "exec_command"}:
            return self._wrapped_prefixed_lines("$ ", str(target), width)
        if name in {"write", "edit", "notebookedit"}:
            return self._wrapped_prefixed_lines("", f"{req.tool_name}: {target}", width)
        return self._wrapped_prefixed_lines("", f"{req.tool_name}: {target}", width)

    def _action_labels(self, req: ApprovalRequest) -> list[str]:
        name = req.tool_name.lower()
        if name in {"write", "edit", "notebookedit"}:
            session = "Yes, and don't ask again for these files (s)"
        elif name in {"bash", "exec_command"}:
            session = "Yes, and don't ask again for this command in this session (s)"
        else:
            session = "Yes, and don't ask again for this exact request this session (s)"
        return [
            "Yes, proceed (y)",
            "No, and tell Igorek what to do differently (esc)",
            session,
        ]

    def _wrapped_prefixed_lines(self, prefix: str, text: str, width: int) -> list[str]:
        import textwrap
        # Normalize the input: collapse embedded newlines and runs of
        # whitespace into single spaces. Without this, ``textwrap.wrap``
        # would preserve internal ``\n`` and we'd render multiple
        # physical lines inside a single body row, which would break
        # the frame border when the terminal prints it.
        cleaned = " ".join(str(text).split())
        max_text = max(10, width - len(prefix) - 2)
        wrapped = textwrap.wrap(
            cleaned, width=max_text,
            replace_whitespace=True, drop_whitespace=True,
            break_long_words=True, break_on_hyphens=False,
        ) or [""]
        lines = []
        for idx, part in enumerate(wrapped):
            p = prefix if idx == 0 else " " * len(prefix)
            lines.append(f"  {p}{part}")
        return lines

    def _wrapped_reason_lines(self, text: str, width: int) -> list[str]:
        import textwrap
        prefix = "Reason: "
        # Collapse newlines / multiple whitespace into single spaces so
        # the frame border stays intact (each wrapped chunk is one
        # physical body row, not multiple).
        cleaned = " ".join(str(text).split())
        max_text = max(10, width - len(prefix) - 2)
        wrapped = textwrap.wrap(
            cleaned, width=max_text,
            replace_whitespace=True, drop_whitespace=True,
            break_long_words=True, break_on_hyphens=False,
        ) or [""]
        if len(wrapped) > self.MAX_REASON_LINES:
            wrapped = wrapped[:self.MAX_REASON_LINES]
            wrapped[-1] = wrapped[-1].rstrip(" .") + "…"
        lines = []
        for idx, part in enumerate(wrapped):
            p = prefix if idx == 0 else " " * len(prefix)
            lines.append(f"  {p}{_format_inline(part)}")
        return lines

    def handle_key(self, key: str) -> Optional[str]:
        """Handle approval key press. Returns result if decided."""
        if key in ("y", "1"):
            return self.allow()
        if key == "enter":
            action = self.selected_action()
            if action == "allow":
                return self.allow()
            if action == "reject":
                self.reject()
                return "reject"
            self.allow_session()
            return self.allow()
        if key in ("n", "2", "escape", "c-c"):
            self.reject()
            return "reject"
        if key in ("s", "3"):
            self.allow_session()
            return self.allow()
        if key in ("left", "up"):
            self.move_action_selection(-1)
        if key in ("right", "down"):
            self.move_action_selection(1)
        return None
