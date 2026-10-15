"""Chat composer — full port of Codex bottom_pane/chat_composer.rs.

Editable prompt input state machine with:
- Multiline text editing
- Key routing (Enter submit, Ctrl+J newline, Tab queue)
- Popup handling (/, @, $)
- History navigation (Up/Down)
- Paste burst detection
- Footer mode management
- Remote image rows
"""
from __future__ import annotations
from typing import Optional, Callable, Any
from ..render.renderable import Renderable, Rect
from .footer import FooterProps, FooterMode, render_footer_from_props, FOOTER_INDENT_COLS
from .chat_composer_history import ChatComposerHistory
from .paste_burst import PasteBurst
from .command_popup import CommandPopup, MAX_POPUP_ROWS
from .file_search_popup import FileSearchPopup
from .skill_popup import SkillPopup

LIVE_PREFIX_COLS = 2


class ActivePopup:
    NONE = 0; COMMAND = 1; FILE = 2; SKILL = 3


class ChatComposer(Renderable):
    """Codex ChatComposer: owns input buffer, popups, footer, and history."""

    def __init__(self):
        # ── text buffer ──
        self.text: str = ""
        self.cursor: int = 0
        self.input_enabled: bool = True
        self.placeholder: str = "Type a message..."

        # ── popups ──
        self.active_popup: int = ActivePopup.NONE
        self.command_popup = CommandPopup()
        self.file_popup = FileSearchPopup()
        self.skill_popup = SkillPopup()

        # ── callbacks ──
        self._on_submit: Optional[Callable[[str], None]] = None
        self._on_queue: Optional[Callable[[str], None]] = None
        self._on_interrupt: Optional[Callable[[], None]] = None
        self._on_change: Optional[Callable[[str], None]] = None

        # ── history ──
        self.history = ChatComposerHistory()

        # ── paste burst ──
        self.paste_burst = PasteBurst()

        # ── footer ──
        self.footer_props = FooterProps()
        self.esc_backtrack_hint: bool = False
        self.quit_shortcut_key: str = "ctrl+c"

        # ── remote images ──
        self.remote_image_urls: list[str] = []
        self._remote_image_selected: int = -1

        # ── enhanced keys ──
        self.enhanced_keys_supported: bool = False

    # ── callbacks ─────────────────────────────────────────────────

    def set_on_submit(self, cb: Callable[[str], None]):
        self._on_submit = cb

    def set_on_queue(self, cb: Callable[[str], None]):
        self._on_queue = cb

    def set_on_interrupt(self, cb: Callable[[], None]):
        self._on_interrupt = cb

    def set_on_change(self, cb: Callable[[str], None]):
        self._on_change = cb

    # ── text editing ──────────────────────────────────────────────

    def set_text(self, text: str):
        self.text = text
        self.cursor = len(text)
        self._notify_change()

    def clear(self):
        self.text = ""
        self.cursor = 0
        self._remote_image_selected = -1
        self._notify_change()

    def insert_char(self, ch: str):
        self.text = self.text[:self.cursor] + ch + self.text[self.cursor:]
        self.cursor += 1
        self._notify_change()

    def insert_text(self, text: str):
        for ch in text:
            if ch == "\n":
                self.insert_newline()
            else:
                self.insert_char(ch)

    def insert_newline(self):
        self.text = self.text[:self.cursor] + "\n" + self.text[self.cursor:]
        self.cursor += 1
        self._notify_change()

    def backspace(self):
        if self.cursor > 0:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1
            self._notify_change()

    def delete(self):
        if self.cursor < len(self.text):
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
            self._notify_change()

    def delete_word_backward(self):
        """Ctrl+W: delete word before cursor."""
        if self.cursor == 0:
            return
        # Find start of previous word
        end = self.cursor
        while self.cursor > 0 and self.text[self.cursor - 1].isspace():
            self.cursor -= 1
        while self.cursor > 0 and not self.text[self.cursor - 1].isspace():
            self.cursor -= 1
        self.text = self.text[:self.cursor] + self.text[end:]
        self._notify_change()

    def move_cursor_left(self):
        if self.cursor > 0:
            self.cursor -= 1

    def move_cursor_right(self):
        if self.cursor < len(self.text):
            self.cursor += 1

    def move_cursor_up(self):
        """Move to previous line at same column."""
        line_start = self.text.rfind("\n", 0, self.cursor)
        if line_start < 0:
            return  # already at first line
        col = self.cursor - line_start - 1
        prev_start = self.text.rfind("\n", 0, line_start)
        if prev_start < 0:
            prev_start = 0
        else:
            prev_start += 1
        prev_len = line_start - prev_start
        self.cursor = prev_start + min(col, prev_len)

    def move_cursor_down(self):
        """Move to next line at same column."""
        line_end = self.text.find("\n", self.cursor)
        if line_end < 0:
            return  # already at last line
        line_start = self.text.rfind("\n", 0, self.cursor)
        if line_start < 0:
            line_start = 0
        else:
            line_start += 1
        col = self.cursor - line_start
        next_start = line_end + 1
        next_end = self.text.find("\n", next_start)
        if next_end < 0:
            next_end = len(self.text)
        next_len = next_end - next_start
        self.cursor = next_start + min(col, next_len)

    def move_cursor_home(self):
        """Ctrl+A: move to start of line."""
        nl = self.text.rfind("\n", 0, self.cursor)
        self.cursor = 0 if nl < 0 else nl + 1

    def move_cursor_end(self):
        """Ctrl+E: move to end of line."""
        nl = self.text.find("\n", self.cursor)
        self.cursor = len(self.text) if nl < 0 else nl

    def kill_line(self):
        """Ctrl+K: delete from cursor to end of line."""
        nl = self.text.find("\n", self.cursor)
        end = len(self.text) if nl < 0 else nl
        self.text = self.text[:self.cursor] + self.text[end:]
        self._notify_change()

    def _notify_change(self):
        if self._on_change:
            self._on_change(self.text)

    # ── submit / queue ────────────────────────────────────────────

    def submit(self):
        """Codex: Enter submits immediately."""
        text = self.text.strip()
        if text and self._on_submit:
            self.history.add_local({"text": text})
            self._on_submit(text)
            self.clear()

    def queue(self):
        """Codex: Tab queues message for later."""
        text = self.text.strip()
        if text and self._on_queue:
            self.history.add_local({"text": text})
            self._on_queue(text)
            self.clear()

    def interrupt(self):
        if self._on_interrupt:
            self._on_interrupt()

    # ── history navigation ────────────────────────────────────────

    def recall_previous(self):
        """Up arrow: recall previous history entry."""
        if not self.history.can_navigate(self.text, self.cursor):
            return
        result = self.history.recall_previous(self.text)
        if result is not None:
            self.set_text(result)

    def recall_next(self):
        """Down arrow: recall next history entry."""
        result = self.history.recall_next()
        if result is not None:
            self.set_text(result)

    # ── remote images ─────────────────────────────────────────────

    def set_remote_images(self, urls: list[str]):
        self.remote_image_urls = urls
        self._remote_image_selected = -1

    def _select_remote_image(self, delta: int):
        if not self.remote_image_urls:
            self._remote_image_selected = -1
            return
        self._remote_image_selected += delta
        self._remote_image_selected = max(-1, min(
            len(self.remote_image_urls) - 1, self._remote_image_selected))

    def _delete_remote_image(self):
        if 0 <= self._remote_image_selected < len(self.remote_image_urls):
            del self.remote_image_urls[self._remote_image_selected]
            self._remote_image_selected = min(
                self._remote_image_selected, len(self.remote_image_urls) - 1)

    # ── key routing ───────────────────────────────────────────────

    def handle_key_event(self, key: str):
        """Codex: route key events to appropriate handler."""
        if not self.input_enabled:
            return

        # Remote image navigation
        if self._remote_image_selected >= 0:
            if key == "up":
                self._select_remote_image(1)
                return
            if key == "down":
                self._select_remote_image(-1)
                return
            if key in ("backspace", "delete"):
                self._delete_remote_image()
                return

        # Popup-aware routing (Codex line 1206-1214)
        if self.active_popup == ActivePopup.COMMAND:
            self._handle_key_event_with_command_popup(key)
            return
        if self.active_popup == ActivePopup.FILE:
            self._handle_key_event_with_file_popup(key)
            return
        if self.active_popup == ActivePopup.SKILL:
            self._handle_key_event_with_skill_popup(key)
            return

        self._handle_key_event_without_popup(key)

    def _handle_key_event_without_popup(self, key: str):
        """Codex: handle key with no active popup."""

        # Enter: submit
        if key == "enter":
            self.submit()
            return

        # Shift+Enter or Ctrl+J: insert newline
        if key in ("c-j", "shift-enter"):
            self.insert_newline()
            return

        # Tab: queue message
        if key == "tab":
            if self.text.strip():
                self.queue()
            return

        # Ctrl+C: interrupt
        if key == "c-c":
            self.interrupt()
            return

        # Escape: toggle backtrack hint
        if key == "escape":
            if self.esc_backtrack_hint:
                self.esc_backtrack_hint = False
            else:
                self.esc_backtrack_hint = True
            return

        # ?: toggle shortcuts overlay when empty
        if key == "?" and not self.text.strip():
            self.footer_props.mode = (
                FooterMode.SHORTCUT_OVERLAY
                if self.footer_props.mode != FooterMode.SHORTCUT_OVERLAY
                else FooterMode.COMPOSER_EMPTY
            )
            return

        # /: open command popup when first char
        if key == "/" and not self.text.strip():
            self.active_popup = ActivePopup.COMMAND
            self.command_popup.on_composer_text_change("/")
            self.insert_char("/")
            return

        # @: open file search popup when first char
        if key == "@" and not self.text.strip():
            self.active_popup = ActivePopup.FILE
            self.file_popup.on_composer_text_change("@")
            self.insert_char("@")
            return

        # $: open skill popup when first char
        if key == "$" and not self.text.strip():
            self.active_popup = ActivePopup.SKILL
            self.skill_popup.on_composer_text_change("$")
            self.insert_char("$")
            return

        # Up/Down: history navigation (at cursor boundaries)
        if key == "up":
            if (self.cursor == 0 or self.cursor == len(self.text)):
                self.recall_previous()
            else:
                self.move_cursor_up()
            return
        if key == "down":
            if (self.cursor == 0 or self.cursor == len(self.text)):
                self.recall_next()
            else:
                self.move_cursor_down()
            return

        # Left/Right: cursor movement
        if key == "left":
            self.move_cursor_left()
            return
        if key == "right":
            self.move_cursor_right()
            return

        # Backspace/Delete
        if key == "backspace":
            self.backspace()
            return
        if key == "delete":
            self.delete()
            return

        # Ctrl+A / Ctrl+E: line boundaries
        if key == "c-a":
            self.move_cursor_home()
            return
        if key == "c-e":
            self.move_cursor_end()
            return

        # Ctrl+K: kill line
        if key == "c-k":
            self.kill_line()
            return

        # Ctrl+W: delete word backward
        if key == "c-w":
            self.delete_word_backward()
            return

        # Single character input
        if len(key) == 1:
            self.insert_char(key)

    def _handle_key_event_with_command_popup(self, key: str):
        """Codex line 1223: handle key when slash command popup is visible."""
        # Up/Down: move selection
        if key in ("up", "c-p"):
            self.command_popup.move_up()
            return
        if key in ("down", "c-n"):
            self.command_popup.move_down()
            return

        # Escape: close popup
        if key == "escape":
            self.active_popup = ActivePopup.NONE
            return

        # Enter or Tab: select command
        if key in ("enter", "tab"):
            sel = self.command_popup.selected_item()
            if sel:
                # Replace composer text with selected command
                self.set_text(sel.command + " ")
            self.active_popup = ActivePopup.NONE
            return

        # Backspace: delete char and update popup (or close if at /)
        if key == "backspace":
            if self.text == "/" or self.text == "":
                self.active_popup = ActivePopup.NONE
                self.backspace()
            else:
                self.backspace()
                self.command_popup.on_composer_text_change(self.text)
            return

        # Single character: insert and update filter
        if len(key) == 1:
            self.insert_char(key)
            self.command_popup.on_composer_text_change(self.text)

    def _handle_key_event_with_file_popup(self, key: str):
        """File search popup key handling."""
        if key in ("up", "c-p"):
            self.file_popup.move_up()
            return
        if key in ("down", "c-n"):
            self.file_popup.move_down()
            return
        if key == "escape":
            self.active_popup = ActivePopup.NONE
            return
        if key in ("enter", "tab"):
            sel = self.file_popup.selected_file()
            if sel:
                self.set_text(sel)
            self.active_popup = ActivePopup.NONE
            return
        if key == "backspace":
            if self.text == "@" or self.text == "":
                self.active_popup = ActivePopup.NONE
                self.backspace()
            else:
                self.backspace()
                self.file_popup.on_composer_text_change(self.text)
            return
        if len(key) == 1:
            self.insert_char(key)
            self.file_popup.on_composer_text_change(self.text)

    def _handle_key_event_with_skill_popup(self, key: str):
        """Skill popup key handling."""
        if key in ("up", "c-p"):
            self.skill_popup.move_up()
            return
        if key in ("down", "c-n"):
            self.skill_popup.move_down()
            return
        if key == "escape":
            self.active_popup = ActivePopup.NONE
            return
        if key in ("enter", "tab"):
            sel = self.skill_popup.selected_skill()
            if sel:
                name, _ = sel
                self.set_text(f"${name} ")
            self.active_popup = ActivePopup.NONE
            return
        if key == "backspace":
            if self.text == "$" or self.text == "":
                self.active_popup = ActivePopup.NONE
                self.backspace()
            else:
                self.backspace()
                self.skill_popup.on_composer_text_change(self.text)
            return
        if len(key) == 1:
            self.insert_char(key)
            self.skill_popup.on_composer_text_change(self.text)

    # ── layout ────────────────────────────────────────────────────

    def layout_areas(self, area: Rect) -> dict:
        """Codex line 572: split area into sub-rects."""
        area_w = area.width if hasattr(area, 'width') else area[2]
        area_h = area.height if hasattr(area, 'height') else area[3]
        area_x = area.x if hasattr(area, 'x') else area[0]
        area_y = area.y if hasattr(area, 'y') else area[1]

        popup_h = 0
        if self.active_popup == ActivePopup.COMMAND:
            popup_h = min(self.command_popup.desired_height() + 1, area_h - 3)
        elif self.active_popup == ActivePopup.SKILL:
            popup_h = min(self.skill_popup.desired_height() + 1, area_h - 3)
        elif self.active_popup != ActivePopup.NONE:
            popup_h = 4

        composer_h = max(3, area_h - popup_h)

        composer_rect = Rect(area_x, area_y, area_w, composer_h)
        textarea_rect = Rect(
            area_x + LIVE_PREFIX_COLS, area_y + 1,
            max(0, area_w - LIVE_PREFIX_COLS - 1),
            max(1, composer_h - 2),
        )
        remote_rect = Rect(area_x, area_y, area_w, 0)
        popup_rect = Rect(area_x, area_y + composer_h, area_w, popup_h)

        return {
            "composer": composer_rect,
            "remote_images": remote_rect,
            "textarea": textarea_rect,
            "popup": popup_rect,
        }

    def desired_height(self, width: int) -> int:
        if self.footer_props.is_task_running:
            return 0
        h = 3
        if self.active_popup == ActivePopup.COMMAND:
            h += self.command_popup.desired_height()
        elif self.active_popup == ActivePopup.SKILL:
            h += self.skill_popup.desired_height()
        elif self.active_popup != ActivePopup.NONE:
            h += 4
        return h + 1

    def render(self, area: Rect, buf):
        if self.footer_props.is_task_running:
            return
        if not self.input_enabled:
            buf.append("  › input disabled")
            return

        prompt = "› "
        text_lines = self.text.split("\n") if self.text else [""]

        # Show popup above cursor if active (Codex renders popup above composer)
        if self.active_popup == ActivePopup.COMMAND:
            width = area.width if hasattr(area, 'width') else (area[2] if isinstance(area, tuple) else 80)
            popup_lines = self.command_popup.render_lines(width)
            for pl in popup_lines:
                buf.append(pl)
        elif self.active_popup == ActivePopup.SKILL:
            width = area.width if hasattr(area, 'width') else (area[2] if isinstance(area, tuple) else 80)
            popup_lines = self.skill_popup.render_lines(width)
            for pl in popup_lines:
                buf.append(pl)

        for i, line in enumerate(text_lines):
            prefix = prompt if i == 0 else "  "
            buf.append(f"{prefix}{line}")

        if self.remote_image_urls:
            for idx, url in enumerate(self.remote_image_urls):
                marker = "►" if idx == self._remote_image_selected else " "
                buf.append(f"  {marker} [Image #{idx + 1}]")

        # Footer does not render here; it renders in BottomPane._FooterRenderable
        # To avoid duplication of 'context left' and '? for shortcuts'.

    def cursor_pos(self, area: Rect) -> Optional[tuple[int, int]]:
        if not self.input_enabled:
            return None
        areas = self.layout_areas(area)
        ta = areas["textarea"]
        line_breaks_before = self.text[:self.cursor].count("\n")
        col_in_line = self.cursor
        if line_breaks_before > 0:
            last_nl = self.text[:self.cursor].rfind("\n")
            col_in_line = self.cursor - last_nl - 1
        return (ta.x + col_in_line, ta.y + line_breaks_before)

    # ── footer mode management ────────────────────────────────────

    def update_footer_mode(self):
        """Set footer mode based on composer state."""
        if self.text.strip():
            self.footer_props.mode = FooterMode.COMPOSER_HAS_DRAFT
        else:
            self.footer_props.mode = FooterMode.COMPOSER_EMPTY
        self.footer_props.is_task_running = False
        self.footer_props.esc_backtrack_hint = self.esc_backtrack_hint
        self.footer_props.quit_shortcut_key = self.quit_shortcut_key
