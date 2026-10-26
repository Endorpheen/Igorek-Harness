"""Main chat widget — full port of Codex chatwidget.rs.

Ports the core architecture: layout composition, history cells,
bottom pane integration, task lifecycle, token/context management,
streaming support, status/account display.
"""
from __future__ import annotations
from typing import Optional, Any, Callable
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
from .app_event import AppEvent
import time, threading

from .render.renderable import FlexRenderable, RenderableItem, Renderable, Rect, Insets
from .cells.history_cell import (
    HistoryCell, TextCell, ToolCallCell, ToolCallGroupCell, StatusCell,
    MarkdownCell, ReasoningCell, ErrorCell, PlanCell,
    SessionHeaderCell, ImageCell,
)
from .bottom_pane.footer import FooterProps, FooterMode
from .status_indicator_widget import StatusIndicatorWidget
from .tui import FrameRequester


# ─── Structs / Enums ─────────────────────────────────────────────

@dataclass
class UserMessage:
    text: str = ""
    local_images: list = field(default_factory=list)
    remote_image_urls: list = field(default_factory=list)

@dataclass
class PendingSteer:
    user_message: UserMessage
    compare_key: str = ""

@dataclass
class TokenUsageInfo:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    context_window: int = 32000
    remaining_tokens: Optional[int] = None

class Notification:
    AGENT_TURN_COMPLETE = "agent_turn_complete"
    APPROVAL_REQUESTED = "approval_requested"
    USER_INPUT_REQUESTED = "user_input_requested"

@dataclass
class CollabAgentMetadata:
    agent_nickname: Optional[str] = None
    agent_role: Optional[str] = None


# ─── ChatWidget ──────────────────────────────────────────────────

class ChatWidget:
    """Codex ChatWidget: main chat surface owning layout, history, bottom pane.

    Layout (Codex line 10781):
      FlexRenderable{
        history (flex=1): active_cell + committed_cells,
        bottom_pane (flex=0): composer + footer + status
      }
    """

    def __init__(self, width: int = 80, model: str = "", branch: str = "",
                 cwd: str = ""):
        # ── layout ──
        self.bottom_pane: Any = None
        self._width = width

        # ── history ──
        self.active_cell: Optional[HistoryCell] = None
        self.active_cell_revision: int = 0
        self.committed_cells: list[HistoryCell] = []
        self.needs_final_message_separator: bool = False

        # ── turn state ──
        self.agent_turn_running: bool = False
        self.had_work_activity: bool = False
        self.turn_start_time: float = 0.0
        self.retry_status_header: Optional[str] = None

        # ── queued input ──
        self.queued_user_messages: deque[UserMessage] = deque()
        self.pending_steers: deque[PendingSteer] = deque()
        self.rejected_steers_queue: deque[UserMessage] = deque()
        self.suppress_queue_autosend: bool = False

        # ── token / context ──
        self.token_info: Optional[TokenUsageInfo] = None
        self.context_window_percent: Optional[int] = None
        self.context_used_tokens: Optional[int] = None

        # ── status ──
        self.status_widget = StatusIndicatorWidget()
        self.status_line_value: Optional[str] = None
        self.status_line_enabled: bool = True
        self.active_agent_label: Optional[str] = None

        # ── session info ──
        self.model: str = model
        self.branch: str = branch
        self.cwd: str = cwd
        self.thread_id: Optional[str] = None
        self.thread_name: Optional[str] = None

        # ── streaming ──
        self.stream_buffer: str = ""
        self.reasoning_buffer: str = ""

        # ── reasoning expand ──
        self._show_reasoning: bool = False

        # ── misc ──
        self.frame_requester = FrameRequester()
        self.interrupt_requested: bool = False
        self._on_redraw: Optional[Callable[[], None]] = None
        self._on_submit_message: Optional[Callable[[str], None]] = None
        self._on_interrupt: Optional[Callable[[], None]] = None
        self._on_exit: Optional[Callable[[], None]] = None

    # ── callbacks ─────────────────────────────────────────────────

    def set_on_redraw(self, cb: Callable[[], None]):
        self._on_redraw = cb

    def set_on_submit_message(self, cb: Callable[[str], None]):
        self._on_submit_message = cb

    def set_on_interrupt(self, cb: Callable[[], None]):
        self._on_interrupt = cb

    def set_on_exit(self, cb: Callable[[], None]):
        self._on_exit = cb

    def request_redraw(self):
        self.frame_requester.request_frame()
        if self._on_redraw:
            self._on_redraw()

    # ── bottom pane ───────────────────────────────────────────────

    def set_bottom_pane(self, pane):
        self.bottom_pane = pane

    # ── history cells ─────────────────────────────────────────────

    def set_active_cell(self, cell: Optional[HistoryCell]):
        self.active_cell = cell
        self.bump_active_cell_revision()

    def flush_active_cell(self):
        """Codex line 5530: move active cell to committed history."""
        if self.active_cell:
            self.committed_cells.append(self.active_cell)
            self.needs_final_message_separator = True
            self.active_cell = None
            self.request_redraw()

    def commit_active_cell(self):
        if self.active_cell:
            self.committed_cells.append(self.active_cell)
            self.active_cell = None

    def add_text_cell(self, text: str, role: str = "assistant"):
        self.committed_cells.append(TextCell(text, role))
        self.request_redraw()

    def add_tool_call_cell(self, tool_name: str, tool_input: dict,
                           status: str = "", elapsed: float = 0,
                           output: str = "", is_running: bool = False):
        self.committed_cells.append(
            ToolCallCell(tool_name, tool_input, status, elapsed, output, is_running))

    def add_status_cell(self, text: str):
        self.committed_cells.append(StatusCell(text))

    def add_error_cell(self, text: str):
        self.committed_cells.append(ErrorCell(text))

    def clear_history(self):
        self.committed_cells.clear()
        self.active_cell = None
        self.needs_final_message_separator = False

    def bump_active_cell_revision(self):
        """Codex line 7192: monotonic cache-bust counter."""
        self.active_cell_revision = (self.active_cell_revision + 1) % (2**63)

    # ── layout ────────────────────────────────────────────────────

    class _HistoryArea(HistoryCell):
        def __init__(self, cells, active, show_reasoning=False):
            self.cells = cells; self.active = active; self.show_reasoning = show_reasoning
        def display_lines(self, width):
            lines = []
            prev_type = None
            for cell in self.cells:
                cur_type = type(cell).__name__
                needs_spacing = prev_type and cur_type != prev_type
                if prev_type in {"ToolCallCell", "ToolCallGroupCell"} and cur_type in {
                    "ToolCallCell", "ToolCallGroupCell"
                }:
                    needs_spacing = True
                if needs_spacing and lines and lines[-1] != "":
                    lines.append("")
                if self.show_reasoning and hasattr(cell, 'expanded_display_lines'):
                    sub = cell.expanded_display_lines(width)
                    if sub and sub[0] == "" and lines and lines[-1] == "":
                        sub = sub[1:]
                    lines.extend(sub)
                else:
                    lines.extend(cell.display_lines(width))
                prev_type = cur_type
            if self.active:
                if lines and lines[-1] != "":
                    lines.append("")
                if self.show_reasoning and hasattr(self.active, 'expanded_display_lines'):
                    sub = self.active.expanded_display_lines(width)
                    if sub and sub[0] == "" and lines and lines[-1] == "":
                        sub = sub[1:]
                    lines.extend(sub)
                else:
                    lines.extend(self.active.display_lines(width))
            return lines
        def desired_height(self, width): return len(self.display_lines(width))
        def cursor_pos(self, area): return None

    def as_renderable(self) -> FlexRenderable:
        """Codex line 10781: FlexRenderable layout."""
        flex = FlexRenderable()
        history = self._HistoryArea(self.committed_cells, self.active_cell, show_reasoning=self._show_reasoning)
        flex.push(1, RenderableItem(history))
        if self.bottom_pane:
            flex.push(0, RenderableItem(self.bottom_pane))
        return flex

    def desired_height(self, width: int) -> int:
        self._width = width
        return self.as_renderable().desired_height(width)

    def render(self, area, buf):
        w = area.width if hasattr(area, 'width') else (area[2] if isinstance(area, tuple) else 80)
        self._width = w
        self.as_renderable().render(area, buf)

    def history_lines(self, width: int = 80) -> list[str]:
        """Only history (committed + active cell), excluding BottomPane."""
        self._width = width
        history = self._HistoryArea(self.committed_cells, self.active_cell)
        return history.display_lines(width)

    def cursor_pos(self, area):
        return self.as_renderable().cursor_pos(area)

    # ── task lifecycle ───────────────────────────────────────────

    def on_task_started(self):
        """Codex line 2274: reset turn state, show Working status."""
        self.agent_turn_running = True
        self.had_work_activity = False
        self.turn_start_time = time.monotonic()
        self.stream_buffer = ""
        self.reasoning_buffer = ""
        self.needs_final_message_separator = False
        self.status_widget = StatusIndicatorWidget()
        if self.bottom_pane and hasattr(self.bottom_pane, 'set_status'):
            self.bottom_pane.set_status(self.status_widget)
        self.request_redraw()

    def on_task_complete(self, last_message: str = ""):
        """Codex line 2303: finalize turn, emit separator, send queued."""
        self.flush_active_cell()
        self.agent_turn_running = False

        if self.had_work_activity and self.needs_final_message_separator:
            self.add_status_cell("─" * 40)
            self.needs_final_message_separator = False

        if self.status_widget:
            self.status_widget.update_header("Ready")

        if self.bottom_pane and hasattr(self.bottom_pane, 'status'):
            self.bottom_pane.status = None

        if not self.suppress_queue_autosend:
            self._maybe_send_next_queued()

        self.request_redraw()

    def on_interrupted(self, reason: str = "user"):
        """Codex line 3022: interrupted turn cleanup."""
        self.agent_turn_running = False

        if self.active_cell:
            self.committed_cells.append(self.active_cell)
            self.active_cell = None

        self.drain_queued_to_pending()
        self.request_redraw()

    def finalize_turn(self):
        """Codex line 2701: finalize active cell as failed if present."""
        if self.active_cell:
            self.committed_cells.append(self.active_cell)
            self.active_cell = None
        self.agent_turn_running = False

    # ── queued input ─────────────────────────────────────────────

    def submit_user_message(self, message: UserMessage):
        """Codex line 5567: submit user message to agent."""
        if self.agent_turn_running:
            self.pending_steers.append(PendingSteer(message))
            self._refresh_pending_preview()
        else:
            self._do_submit_message(message)

    def queue_user_message(self, message: UserMessage):
        """Codex line 5558: queue message for later submission."""
        self.queued_user_messages.append(message)
        self._refresh_pending_preview()

    def _do_submit_message(self, message: UserMessage):
        """Actually submit the message."""
        self.add_text_cell(message.text, role="user")
        self.had_work_activity = True
        if self._on_submit_message:
            self._on_submit_message(message.text)
        self.request_redraw()

    def _maybe_send_next_queued(self):
        """Codex line 7231: send next queued input after task completes."""
        if self.queued_user_messages and not self.suppress_queue_autosend:
            msg = self.queued_user_messages.popleft()
            self._do_submit_message(msg)

    def drain_queued_to_pending(self):
        """Drain all queues into pending_inputs (for external processing)."""
        result = []
        while self.queued_user_messages:
            result.append(self.queued_user_messages.popleft().text)
        while self.rejected_steers_queue:
            result.append(self.rejected_steers_queue.popleft().text)
        for steer in list(self.pending_steers):
            result.append(steer.user_message.text)
        self.pending_steers.clear()
        return result

    def _refresh_pending_preview(self):
        """Codex line 7246: update bottom pane with queued messages."""
        if self.bottom_pane and hasattr(self.bottom_pane, 'pending_preview'):
            self.bottom_pane.pending_preview.queued_messages = [
                m.text for m in self.queued_user_messages
            ]
            self.request_redraw()

    # ── token / context ──────────────────────────────────────────

    def set_token_info(self, info: Optional[TokenUsageInfo]):
        """Codex line 2538: update token usage."""
        self.token_info = info
        if info and info.context_window > 0 and info.remaining_tokens is not None:
            self.context_window_percent = int(
                info.remaining_tokens / info.context_window * 100)
            self.context_used_tokens = info.context_window - info.remaining_tokens
        else:
            self.context_window_percent = None
            self.context_used_tokens = None
        self._update_footer_context()

    def context_remaining_percent(self) -> Optional[int]:
        return self.context_window_percent

    def _update_footer_context(self):
        """Sync context info to footer props."""
        if self.bottom_pane and hasattr(self.bottom_pane, 'footer_props'):
            self.bottom_pane.footer_props.context_window_percent = (
                self.context_window_percent)
            self.bottom_pane.footer_props.context_window_used_tokens = (
                self.context_used_tokens)

    # ── status / footer ──────────────────────────────────────────

    def set_status_line(self, text: Optional[str]):
        self.status_line_value = text
        if self.bottom_pane and hasattr(self.bottom_pane, 'footer_props'):
            self.bottom_pane.footer_props.status_line_value = text
        self.request_redraw()

    def set_task_running(self, running: bool):
        self.agent_turn_running = running
        if self.bottom_pane and hasattr(self.bottom_pane, 'footer_props'):
            self.bottom_pane.footer_props.is_task_running = running

    # ── streaming ────────────────────────────────────────────────

    def handle_streaming_delta(self, delta: str):
        """Codex line 4133: feed streaming text delta."""
        self.stream_buffer += delta
        if "\n" in self.stream_buffer:
            lines = self.stream_buffer.split("\n")
            remainder = lines.pop()
            self.stream_buffer = remainder
            for line in lines:
                if not self.active_cell or not self.active_cell.is_stream_continuation():
                    self.flush_active_cell()
                    self.active_cell = MarkdownCell(line + "\n")
                else:
                    self.active_cell.text = (self.active_cell.text or "") + line + "\n"
        self.request_redraw()

    def handle_stream_finished(self):
        """Finalize the raw Markdown message without splitting its last line."""
        if self.stream_buffer:
            if isinstance(self.active_cell, MarkdownCell):
                self.active_cell.text += self.stream_buffer
            else:
                self.flush_active_cell()
                self.active_cell = MarkdownCell(self.stream_buffer)
            self.stream_buffer = ""
        if self.active_cell:
            self.flush_active_cell()
        self.request_redraw()

    # ── interrupt / quit ─────────────────────────────────────────

    def interrupt(self):
        """Request agent interruption."""
        self.interrupt_requested = True
        if self._on_interrupt:
            self._on_interrupt()
        self.on_interrupted("user")
        self.request_redraw()

    def quit(self):
        if self._on_exit:
            self._on_exit()

    # ── streaming with chunking ─────────────────────────────────────

    def handle_agent_message_delta(self, delta: str):
        """Codex: feed agent message text delta."""
        self.stream_buffer += delta

        # Newline-gated commit: extract complete lines
        while "\n" in self.stream_buffer:
            line, self.stream_buffer = self.stream_buffer.split("\n", 1)
            if not self.active_cell or not isinstance(self.active_cell, MarkdownCell):
                self.flush_active_cell()
                self.active_cell = MarkdownCell(line + "\n")
            else:
                # Append to existing markdown cell
                self.active_cell.text = (self.active_cell.text or "") + line + "\n"
            self.had_work_activity = True
        self.request_redraw()

    def handle_agent_message_complete(self):
        """Finalize the raw Markdown message without splitting its last line."""
        if self.stream_buffer:
            if isinstance(self.active_cell, MarkdownCell):
                self.active_cell.text += self.stream_buffer
            else:
                self.flush_active_cell()
                self.active_cell = MarkdownCell(self.stream_buffer)
            self.stream_buffer = ""
        if self.active_cell:
            self.flush_active_cell()
        self.request_redraw()

    def handle_reasoning_delta(self, delta: str):
        """Codex: reasoning/thinking text delta."""
        self.reasoning_buffer += delta
        self.request_redraw()

    def handle_reasoning_complete(self):
        """Codex: reasoning complete — add to history."""
        if self.reasoning_buffer:
            self.committed_cells.append(ReasoningCell(self.reasoning_buffer))
            self.reasoning_buffer = ""
            self.request_redraw()

    # ── tool call handling ─────────────────────────────────────────

    def handle_tool_call_started(self, tool_name: str, tool_input: dict,
                                 tool_id: str = ""):
        """Codex: tool call started — show Running indicator."""
        cell = ToolCallCell(tool_name, tool_input, is_running=True)
        self.committed_cells.append(cell)
        self.had_work_activity = True
        self.request_redraw()

    def handle_tool_call_completed(self, tool_name: str, tool_input: dict,
                                   status: str, elapsed: float = 0,
                                   output: str = "", tool_id: str = ""):
        """Codex: tool call completed — replace running with result."""
        # Replace last running tool call with completed one
        if (self.committed_cells and
            isinstance(self.committed_cells[-1], ToolCallCell) and
            self.committed_cells[-1].is_running and
            self.committed_cells[-1].tool_name == tool_name):
            self.committed_cells.pop()

        cell = ToolCallCell(tool_name, tool_input, status, elapsed, output)
        self.committed_cells.append(cell)
        self.had_work_activity = True
        self.request_redraw()

    # ── error handling ─────────────────────────────────────────────

    def handle_error(self, message: str):
        """Codex: display error message."""
        self.committed_cells.append(ErrorCell(message))
        self.request_redraw()

    def handle_warning(self, message: str):
        """Codex: display warning in status."""
        self.committed_cells.append(StatusCell(f"  ⚠ {message}"))
        self.request_redraw()

    # ── plan mode ──────────────────────────────────────────────────

    def handle_plan_update(
        self,
        plan: list[dict[str, Any] | str],
        explanation: str | None = None,
    ):
        """Append a Codex-style plan revision to the history."""
        self.committed_cells.append(PlanCell(plan, explanation=explanation))
        self.request_redraw()

    # ── session management ─────────────────────────────────────────

    def set_session_info(self, model: str, branch: str, cwd: str,
                         thread_id: str = ""):
        """Set session info and render header."""
        self.model = model
        self.branch = branch
        self.cwd = cwd
        self.thread_id = thread_id

    def show_session_header(self):
        """Render session header cell."""
        header = SessionHeaderCell(self.model, self.branch, self.cwd)
        self.committed_cells.insert(0, header)
        self.request_redraw()

    # ── status management ──────────────────────────────────────────

    def set_status(self, header: str, details: str = ""):
        """Codex line 1740: set status indicator header + details."""
        self.status_widget.update_header(header)
        if details:
            self.status_widget.update_details(details)
        if self.bottom_pane and hasattr(self.bottom_pane, 'status'):
            self.bottom_pane.status = self.status_widget
        self.request_redraw()

    def clear_status(self):
        """Remove status indicator."""
        if self.bottom_pane and hasattr(self.bottom_pane, 'status'):
            self.bottom_pane.status = None
        self.request_redraw()

    # ── copyable output ────────────────────────────────────────────

    def get_last_output(self) -> Optional[str]:
        """Get last assistant message for clipboard."""
        for cell in reversed(self.committed_cells):
            if isinstance(cell, (TextCell, MarkdownCell)):
                return "\n".join(cell.display_lines(self._width))
        return None

    # ── pre-draw tick ──────────────────────────────────────────────

    def pre_draw_tick(self):
        """Codex line 4022: called before each draw frame."""
        pass  # Bottom pane tick, terminal title animation would go here

# ---------------------------------------------------------------------------
# one-to-one port extensions (Codex chatwidget.rs lines 525–2028)
# ---------------------------------------------------------------------------

from typing import Protocol, List, Dict, Tuple, Deque, Iterable, Iterator
from enum import Enum

# Forward-imports from sibling modules
from .key_hint import KeyEvent, KeyCode, KeyModifiers, KeyEventKind
from .token_usage import TokenUsage
from .app_event_sender import AppEventSender


# ---------------------------------------------------------------------------
# Sub-enums / sub-types (port of codex chatwidget.rs lines 506–794)
# ---------------------------------------------------------------------------

class ExternalEditorStateKind(Enum):
    """Port of ``ChatWidget::ExternalEditorState`` enum."""
    None_ = "none"
    Pending = "pending"
    Active = "active"


@dataclass
class ExternalEditorState:
    """Port one-to-one of ``codex::chatwidget::ExternalEditorState``."""
    state: ExternalEditorStateKind = ExternalEditorStateKind.None_
    current: Optional[Any] = None  # current prompt (if any)
    pending_inputs: Deque[Any] = field(default_factory=deque)


class CodexOpTarget(Enum):
    """Port one-to-one of ``codex::chatwidget::CodexOpTarget`` (lines 724–732)."""
    BuiltIn = "built_in"
    AppServer = "app_server"


class ActiveCellTranscriptKeyKind(Enum):
    """Port one-to-one of ``ActiveCellTranscriptKey`` enum (lines 734–752)."""
    AgentMessage = "agent_message"
    ProposedPlan = "proposed_plan"


@dataclass
class ActiveCellTranscriptKey:
    """Port one-to-one of ``codex::chatwidget::ActiveCellTranscriptKey``."""
    kind: ActiveCellTranscriptKeyKind
    cell_id: Optional[str] = None
    revision: int = 0


class InterruptedTurnNoticeMode(Enum):
    """Port one-to-one of ``codex::chatwidget::InterruptedTurnNoticeMode`` (lines 754–759)."""
    WithNotice = "with_notice"
    WithoutNotice = "without_notice"


class ReplayKind(Enum):
    """Port one-to-one of ``codex::chatwidget::ReplayKind`` (lines 761–765)."""
    Initial = "initial"
    ThreadSwitch = "thread_switch"


class SessionConfiguredDisplay(Enum):
    """Port one-to-one of ``codex::chatwidget::SessionConfiguredDisplay`` (lines 767–774)."""
    None_ = "none"
    AfterFork = "after_fork"
    AfterResume = "after_resume"


class PlanModeNudgeScope(Enum):
    """Port one-to-one of ``codex::chatwidget::PlanModeNudgeScope`` (lines 776–782)."""
    NewSession = "new_session"
    Resume = "resume"


class TurnAbortReason(Enum):
    """Port one-to-one of ``codex::chatwidget::TurnAbortReason`` (lines 784–792)."""
    Interrupted = "interrupted"
    Replaced = "replaced"
    ReviewFailed = "review_failed"


# ---------------------------------------------------------------------------
# ChatWidgetInit (port of codex lines 478–504)
# ---------------------------------------------------------------------------

@dataclass
class ChatWidgetInit:
    """Port one-to-one of ``codex::chatwidget::ChatWidgetInit``.

    The full Codex struct carries:
      - config: Config
      - frame_requester: FrameRequester
      - app_event_tx: AppEventSender
      - latest_app_version: Option<String>
      - model: String
      - enhanced_keys_supported: bool
      - auth_mode: Option<AuthMode>
      - model_catalog: ModelCatalog (Arc)
      - has_chatgpt_account: bool
      - feedback: CodexFeedback
      - initial_user_message: Option<UserMessage>
      - enhanced_keys_supported: bool
      - session_selection: SessionSelection
      - app_server: AppServerSession
      - feedback_audience: Option<FeedbackAudience>
      - status_account_display: Option<StatusAccountDisplay>
      - initial_plan_type: Option<PlanType>
      - has_chatgpt_account: bool
      - is_first_run: bool
      - status_line_items: Option<Vec<StatusLineItem>>
      - terminal_title_items: Option<Vec<TerminalTitleItem>>
      - session_log: SessionLog
      - state_db: Option<StateDbHandle>
      - startup_hooks_browser: Option<HooksListEntry>
      - environment_manager: Arc<EnvironmentManager>
    """
    config: Any
    frame_requester: Any
    app_event_tx: AppEventSender
    model: str = ""
    enhanced_keys_supported: bool = False
    initial_user_message: Optional[Any] = None
    is_first_run: bool = False


# ---------------------------------------------------------------------------
# ThreadItemRenderSource (port of codex lines 800–816)
# ---------------------------------------------------------------------------

class ThreadItemRenderSourceKind(Enum):
    """Port one-to-one of ``codex::chatwidget::ThreadItemRenderSource``."""
    Live = "live"
    ReplayInitial = "replay_initial"
    ReplayThreadSwitch = "replay_thread_switch"


@dataclass
class ThreadItemRenderSource:
    """Port one-to-one of ``codex::chatwidget::ThreadItemRenderSource``."""
    kind: ThreadItemRenderSourceKind

    def is_replay(self) -> bool:
        return self.kind != ThreadItemRenderSourceKind.Live

    def replay_kind(self) -> Optional[ReplayKind]:
        if self.kind == ThreadItemRenderSourceKind.ReplayInitial:
            return ReplayKind.Initial
        if self.kind == ThreadItemRenderSourceKind.ReplayThreadSwitch:
            return ReplayKind.ThreadSwitch
        return None


# ---------------------------------------------------------------------------
# Helper fns (port of codex lines 794–886)
# ---------------------------------------------------------------------------

def contains_plan_keyword(text: str) -> bool:
    """Port one-to-one of ``codex::chatwidget::contains_plan_keyword``.

    Heuristic: a plan-mode nudge is shown when the user message contains the
    word "plan" (case-insensitive) as a separate token.
    """
    lowered = text.lower()
    return any(tok == "plan" for tok in lowered.split())


def exec_approval_request_from_params(params: Any) -> Any:
    """Port one-to-one of ``codex::chatwidget::exec_approval_request_from_params``.

    Constructs an ``ApprovalRequest`` from app-server ``ExecApprovalRequest`` params.
    """
    # TODO: depends on bottom_pane.ApprovalRequest
    return params


def patch_approval_request_from_params(params: Any) -> Any:
    """Port one-to-one of ``codex::chatwidget::patch_approval_request_from_params``."""
    return params


def request_permissions_from_params(params: Any) -> Any:
    """Port one-to-one of ``codex::chatwidget::request_permissions_from_params``."""
    return params


def token_usage_info_from_app_server(token_usage: Any) -> Any:
    """Port one-to-one of ``codex::chatwidget::token_usage_info_from_app_server``."""
    # TODO: depends on token_usage_info dataclass + ThreadTokenUsage
    return token_usage


def has_websocket_timing_metrics(summary: Any) -> bool:
    """Port one-to-one of ``codex::chatwidget::has_websocket_timing_metrics``."""
    return getattr(summary, "ws_total_ms", 0) > 0 or getattr(summary, "ws_count", 0) > 0


def extract_first_bold(s: str) -> Optional[str]:
    """Port one-to-one of ``codex::chatwidget::extract_first_bold``.

    Extracts the contents of the first ``**...**`` span in markdown ``s``.
    Returns ``None`` if no such span exists.
    """
    if "**" not in s:
        return None
    parts = s.split("**")
    if len(parts) < 3:
        return None
    return parts[1]


# ---------------------------------------------------------------------------
# ChatWidget struct fields (port of codex lines 525–723, the full state)
# ---------------------------------------------------------------------------

# The current ChatWidget class above is functional but holds only ~30 fields.
# The full codex struct holds 60+ fields. We add the missing ones as
# instance attributes via a helper, preserving the one-to-one field layout.

def _init_chatwidget_state(widget: "ChatWidget") -> None:
    """Initialize the full one-to-one ChatWidget state (port of codex lines 525–723).

    All fields are present in the codex struct; missing ones in the legacy
    Python port are added here with their default values.
    """
    # ----- Layout / render -----
    widget.bottom_pane: Any = getattr(widget, "bottom_pane", None)
    widget.frame_requester: Any = getattr(widget, "frame_requester", None)
    widget.app_event_tx: Optional[AppEventSender] = getattr(widget, "app_event_tx", None)
    widget.config: Any = getattr(widget, "config", None)

    # ----- History cells -----
    widget.history_cells: List[Any] = []  # the full transcript (not just active)
    widget.active_cell: Optional[Any] = None
    widget.active_cell_revision: int = 0
    widget.active_cell_transcript_key: Optional[ActiveCellTranscriptKey] = None
    widget.committed_cells: List[Any] = []
    widget.needs_final_message_separator: bool = False

    # ----- Queues / pending -----
    widget.queued_user_messages: Deque[Any] = deque()
    widget.pending_steers: Deque[Any] = deque()
    widget.rejected_steers_queue: Deque[Any] = deque()
    widget.suppress_queue_autosend: bool = False
    widget.last_known_thread_name: Optional[str] = None

    # ----- Turn state -----
    widget.agent_turn_running: bool = False
    widget.had_work_activity: bool = False
    widget.turn_start_time: float = 0.0
    widget.retry_status_header: Optional[str] = None
    widget.last_rendered_user_turn_id: Optional[str] = None
    widget.last_rendered_agent_turn_id: Optional[str] = None
    widget.review_mode: bool = False
    widget.review_hint: Optional[str] = None
    widget.is_review_mode_replay: bool = False
    widget.turn_abort_reason: Optional[TurnAbortReason] = None

    # ----- Token / context -----
    widget.token_info: Optional[Any] = None
    widget.pre_review_token_info: Optional[Any] = None
    widget.context_window_percent: Optional[int] = None
    widget.context_used_tokens: Optional[int] = None
    widget.last_total_token_usage: TokenUsage = TokenUsage()

    # ----- Status / session info -----
    widget.status_line_value: Optional[str] = None
    widget.status_line_enabled: bool = True
    widget.status_line_branch: Optional[str] = None
    widget.status_line_git_summary: Any = None
    widget.active_agent_label: Optional[str] = None
    widget.thread_id: Optional[str] = None
    widget.thread_name: Optional[str] = None
    widget.model: str = getattr(widget, "model", "")
    widget.branch: str = getattr(widget, "branch", "")
    widget.cwd: str = getattr(widget, "cwd", "")
    widget.session_configured_display: SessionConfiguredDisplay = SessionConfiguredDisplay.None_
    widget.entered_trust_nux: bool = False

    # ----- Streaming -----
    widget.stream_buffer: str = ""
    widget.reasoning_buffer: str = ""
    widget.plan_buffer: str = ""
    widget.is_plan_streaming: bool = False
    widget.current_stream_width_cached: Optional[int] = None

    # ----- Reasoning -----
    widget.reasoning_summary: Optional[str] = None
    widget.reasoning_buffer: str = ""

    # ----- Plugins / skills / hooks / mcp -----
    widget.plugins_for_mentions: Optional[List[Any]] = None
    widget.last_skills_refresh: Optional[float] = None
    widget.skill_errors_for_cwd: Dict[str, List[Any]] = {}
    widget.app_link_view_state: Optional[Any] = None

    # ----- Hooks -----
    widget.hooks: Optional[List[Any]] = None

    # ----- Pets -----
    widget.ambient_pet: Any = None
    widget.pet_picker_open: bool = False

    # ----- Pets / onboarding -----
    widget.entered_trust_nux: bool = False

    # ----- Permissions / approval -----
    widget.pending_approval: Optional[Any] = None
    widget.approval_modal_visible: bool = False

    # ----- Resume picker -----
    widget.resume_picker_visible: bool = False
    widget.resume_picker_data: Any = None

    # ----- Diff -----
    widget.last_diff_text: Optional[str] = None
    widget.diff_summary: Any = None

    # ----- Realtime audio / voice -----
    widget.realtime_conversation_enabled: bool = False
    widget.realtime_audio_level: int = 0
    widget.voice_meter_text: str = ""
    widget.voice_meter_id: str = ""

    # ----- Auto-review denials -----
    widget.pending_auto_review_denials: List[Any] = []

    # ----- Keymap -----
    widget.keymap_overrides: Dict[str, Any] = {}

    # ----- Status widgets -----
    widget.status_widget: Any = None
    widget.status_line_branch: Optional[str] = None
    widget.status_line_git_summary: Any = None

    # ----- Feedback -----
    widget.feedback_modal: Optional[Any] = None

    # ----- External editor -----
    widget.external_editor_state: ExternalEditorState = ExternalEditorState()

    # ----- Restart / cleanup -----
    widget.background_terminals: List[Any] = []

    # ----- Approval request modal -----
    widget.approval_request_modal: Optional[Any] = None
    widget.mcp_elicitation_modal: Optional[Any] = None
    widget.user_input_request_modal: Optional[Any] = None

    # ----- Window resize -----
    widget.last_terminal_size: Optional[Tuple[int, int]] = None

    # ----- Plan mode -----
    widget.plan_mode_active: bool = False
    widget.plan_mode_nudge_scope: Optional[PlanModeNudgeScope] = None

    # ----- User message history (for `copy last message`) -----
    widget.last_visible_user_turn: Optional[Any] = None
    widget.last_user_turn_text: str = ""

    # ----- Interrupt flag -----
    widget.interrupt_requested: bool = False

    # ----- Run-loop control -----
    widget.quit_requested: bool = False
    widget.exit_requested: bool = False


def init_chatwidget_full_state(widget: "ChatWidget") -> None:
    """Public hook: call this after ``ChatWidget.__init__`` to install the
    full one-to-one state (port of codex struct field initialization).
    """
    _init_chatwidget_state(widget)


# ---------------------------------------------------------------------------
# Additional ChatWidget methods (port of codex lines 886–1973)
# ---------------------------------------------------------------------------

def _chatwidget_restore_retry_status_header_if_present(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::restore_retry_status_header_if_present``."""
    if widget.retry_status_header is None:
        return
    widget.add_status_cell(widget.retry_status_header)
    widget.retry_status_header = None


def _chatwidget_record_agent_markdown(widget: "ChatWidget", message: str) -> None:
    """Port one-to-one of ``ChatWidget::record_agent_markdown``.

    Records the agent's markdown response for the active cell / consolidation.
    """
    if widget.active_cell is None:
        return
    if hasattr(widget.active_cell, "record_markdown"):
        widget.active_cell.record_markdown(message)


def _chatwidget_record_visible_user_turn_for_copy(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::record_visible_user_turn_for_copy``."""
    last = widget.last_visible_user_turn
    widget.last_user_turn_text = getattr(last, "text", "")


def _chatwidget_apply_token_info(widget: "ChatWidget", info: Any) -> None:
    """Port one-to-one of ``ChatWidget::apply_token_info``."""
    widget.token_info = info
    widget.context_window_percent = _chatwidget_context_remaining_percent(widget, info)
    widget.context_used_tokens = _chatwidget_context_used_tokens(widget, info,
        percent_known=widget.context_window_percent is not None)
    if widget.bottom_pane is not None and hasattr(widget.bottom_pane, "set_token_info"):
        widget.bottom_pane.set_token_info(info)


def _chatwidget_context_remaining_percent(widget: "ChatWidget", info: Any) -> Optional[int]:
    """Port one-to-one of ``ChatWidget::context_remaining_percent``."""
    used = getattr(info, "percent_of_context_window_remaining", None)
    if used is None:
        return None
    try:
        return int(used)
    except (TypeError, ValueError):
        return None


def _chatwidget_context_used_tokens(widget: "ChatWidget", info: Any, percent_known: bool) -> Optional[int]:
    """Port one-to-one of ``ChatWidget::context_used_tokens``."""
    if percent_known:
        return getattr(info, "tokens_in_context_window", None)
    return getattr(info, "blended_total", None)


def _chatwidget_restore_pre_review_token_info(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::restore_pre_review_token_info``."""
    if widget.pre_review_token_info is not None:
        _chatwidget_apply_token_info(widget, widget.pre_review_token_info)
        widget.pre_review_token_info = None


def _chatwidget_flush_active_cell(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::flush_active_cell``."""
    if widget.active_cell is None:
        return
    widget.committed_cells.append(widget.active_cell)
    widget.active_cell = None
    widget.active_cell_transcript_key = None
    widget.needs_final_message_separator = True
    widget.request_redraw()


def _chatwidget_add_boxed_history(widget: "ChatWidget", cell: Any) -> None:
    """Port one-to-one of ``ChatWidget::add_boxed_history``."""
    widget.committed_cells.append(cell)
    widget.needs_final_message_separator = True
    widget.request_redraw()


def _chatwidget_enter_review_mode_with_hint(widget: "ChatWidget", hint: str, from_replay: bool) -> None:
    """Port one-to-one of ``ChatWidget::enter_review_mode_with_hint``."""
    widget.review_mode = True
    widget.review_hint = hint
    widget.is_review_mode_replay = from_replay


def _chatwidget_exit_review_mode_after_item(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::exit_review_mode_after_item``."""
    widget.review_mode = False
    widget.review_hint = None
    widget.is_review_mode_replay = False


def _chatwidget_finalize_active_cell_as_failed(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::finalize_active_cell_as_failed``."""
    if widget.active_cell is None:
        return
    if hasattr(widget.active_cell, "mark_as_failed"):
        widget.active_cell.mark_as_failed()
    widget.committed_cells.append(widget.active_cell)
    widget.active_cell = None
    widget.active_cell_transcript_key = None
    widget.request_redraw()


def _chatwidget_clean_background_terminals(widget: "ChatWidget") -> None:
    """Port one-to-one of ``ChatWidget::clean_background_terminals``.

    Cleans up any background terminal processes owned by the widget.
    """
    for term in list(widget.background_terminals):
        if hasattr(term, "cleanup"):
            try:
                term.cleanup()
            except Exception:
                pass
    widget.background_terminals.clear()


def _chatwidget_plugins_for_mentions(widget: "ChatWidget") -> Optional[List[Any]]:
    """Port one-to-one of ``ChatWidget::plugins_for_mentions``."""
    return widget.plugins_for_mentions


def _chatwidget_placeholder_session_header_cell(config: Any) -> Any:
    """Port one-to-one of ``ChatWidget::placeholder_session_header_cell``."""
    # TODO: depends on history_cell.SessionInfoCell
    return None


def _chatwidget_apply_session_info_cell(widget: "ChatWidget", cell: Any) -> None:
    """Port one-to-one of ``ChatWidget::apply_session_info_cell``."""
    widget.committed_cells.append(cell)
    widget.request_redraw()


def _chatwidget_add_app_server_stub_message(widget: "ChatWidget", feature: str) -> None:
    """Port one-to-one of ``ChatWidget::add_app_server_stub_message``.

    Renders a "feature not yet wired to app server" notice for the given feature.
    """
    widget.add_status_cell(f"Feature '{feature}' not yet wired to app server")


def _chatwidget_rename_confirmation_cell(name: str, thread_id: Optional[str]) -> Any:
    """Port one-to-one of ``ChatWidget::rename_confirmation_cell``."""
    # TODO: depends on PlainHistoryCell
    return None


def _chatwidget_current_stream_width(widget: "ChatWidget", reserved_cols: int) -> Optional[int]:
    """Port one-to-one of ``ChatWidget::current_stream_width``."""
    if widget.last_terminal_size is None:
        return None
    width, _ = widget.last_terminal_size
    if width <= reserved_cols:
        return None
    return width - reserved_cols


def _chatwidget_is_plan_streaming_in_tui(widget: "ChatWidget") -> bool:
    """Port one-to-one of ``ChatWidget::is_plan_streaming_in_tui``."""
    return widget.is_plan_streaming


def _chatwidget_take_remote_image_urls(widget: "ChatWidget") -> List[str]:
    """Port one-to-one of ``ChatWidget::take_remote_image_urls``.

    Drains the remote_image_urls queue and returns them as a flat list.
    """
    if widget.active_cell is None:
        return []
    if hasattr(widget.active_cell, "take_remote_image_urls"):
        return list(widget.active_cell.take_remote_image_urls())
    return []


def _chatwidget_refresh_skills_for_current_cwd(widget: "ChatWidget", force_reload: bool) -> None:
    """Port one-to-one of ``ChatWidget::refresh_skills_for_current_cwd``."""
    if widget.app_event_tx is not None:
        widget.app_event_tx.list_skills([Path(widget.cwd)], force_reload)


def _chatwidget_append_message_history_entry(widget: "ChatWidget", text: str) -> None:
    """Port one-to-one of ``ChatWidget::append_message_history_entry``."""
    if widget.app_event_tx is None:
        return
    if widget.thread_id is None:
        return
    widget.app_event_tx.send(AppEvent.append_message_history_entry(widget.thread_id, text))


def _chatwidget_on_list_skills(widget: "ChatWidget", ev: Any) -> None:
    """Port one-to-one of ``ChatWidget::on_list_skills``.

    Handles ``SkillsListResponse`` and updates the chat widget state.
    """
    # TODO: depends on full skills state
    pass


# ---------------------------------------------------------------------------
# Event dispatch (port of codex lines 886–1950, the main `impl ChatWidget` block)
# ---------------------------------------------------------------------------

def chatwidget_handle_event(widget: "ChatWidget", event: Any) -> bool:
    """Port one-to-one of ``ChatWidget::handle_event``.

    Dispatches key / mouse / paste / draw events to the right handler.
    Returns True if the event was consumed.
    """
    if isinstance(event, KeyEvent):
        return _chatwidget_handle_key(widget, event)
    return False


def _chatwidget_handle_key(widget: "ChatWidget", key_event: KeyEvent) -> bool:
    """Port one-to-one of ``ChatWidget::handle_key`` (the legacy high-level handler).

    The codex key dispatch is split into:
      1. ``handle_key_event`` — top-level (in ``chatwidget/keymap.rs``)
      2. ``handle_key`` — widget-level fallback
      3. Bottom-pane input handling

    The Python port routes the event to the bottom pane if available,
    otherwise to the widget-level handlers.
    """
    # Bottom pane has first crack
    if widget.bottom_pane is not None and hasattr(widget.bottom_pane, "handle_key_event"):
        if widget.bottom_pane.handle_key_event(key_event):
            return True
    return False


def chatwidget_handle_codex_event(widget: "ChatWidget", event: Any) -> None:
    """Port one-to-one of ``ChatWidget::handle_codex_event``.

    Routes an app-server event (``ServerNotification`` / ``ServerRequest`` /
    ``ClientResponse``) to the matching handler. This is the high-level
    entry point used by ``App::run`` to feed events into the widget.
    """
    # TODO: depends on app_server_event_targets
    pass


# ---------------------------------------------------------------------------
# Drop / cleanup (port of codex lines 1973–1997)
# ---------------------------------------------------------------------------

def _chatwidget_drop(widget: "ChatWidget") -> None:
    """Port one-to-one of ``impl Drop for ChatWidget``.

    Cleans up background terminals and any other owned resources.
    """
    _chatwidget_clean_background_terminals(widget)


# Monkey-patch __init__ / __del__ on ChatWidget to integrate the full state.
_original_chatwidget_init = ChatWidget.__init__
_original_chatwidget_del = getattr(ChatWidget, "__del__", lambda self: None)


def _patched_chatwidget_init(self, *args, **kwargs) -> None:
    _original_chatwidget_init(self, *args, **kwargs)
    init_chatwidget_full_state(self)


def _patched_chatwidget_del(self) -> None:
    try:
        _chatwidget_drop(self)
    finally:
        _original_chatwidget_del(self)


ChatWidget.__init__ = _patched_chatwidget_init
ChatWidget.__del__ = _patched_chatwidget_del


# Bind the helper fns to the class as methods (one-to-one method names).
# This makes the port structurally one-to-one even though the implementation
# lives in free fns (Python idiom — equivalent to Rust `impl ChatWidget`).

def _method(self, *args, **kwargs):
    """Trampoline that invokes the free fn bound to the widget."""
    name = _method._name  # set per-method below
    fn = globals()[name]
    return fn(self, *args, **kwargs)


# Bind each helper fn as a method (preserves the codex method name).
_helper_names = [
    ("restore_retry_status_header_if_present", _chatwidget_restore_retry_status_header_if_present),
    ("record_agent_markdown", _chatwidget_record_agent_markdown),
    ("record_visible_user_turn_for_copy", _chatwidget_record_visible_user_turn_for_copy),
    ("apply_token_info", _chatwidget_apply_token_info),
    ("context_remaining_percent", _chatwidget_context_remaining_percent),
    ("context_used_tokens", _chatwidget_context_used_tokens),
    ("restore_pre_review_token_info", _chatwidget_restore_pre_review_token_info),
    ("flush_active_cell", _chatwidget_flush_active_cell),
    ("add_boxed_history", _chatwidget_add_boxed_history),
    ("enter_review_mode_with_hint", _chatwidget_enter_review_mode_with_hint),
    ("exit_review_mode_after_item", _chatwidget_exit_review_mode_after_item),
    ("finalize_active_cell_as_failed", _chatwidget_finalize_active_cell_as_failed),
    ("clean_background_terminals", _chatwidget_clean_background_terminals),
    ("plugins_for_mentions", _chatwidget_plugins_for_mentions),
    ("placeholder_session_header_cell", staticmethod(_chatwidget_placeholder_session_header_cell)),
    ("apply_session_info_cell", _chatwidget_apply_session_info_cell),
    ("add_app_server_stub_message", _chatwidget_add_app_server_stub_message),
    ("rename_confirmation_cell", staticmethod(_chatwidget_rename_confirmation_cell)),
    ("current_stream_width", _chatwidget_current_stream_width),
    ("is_plan_streaming_in_tui", _chatwidget_is_plan_streaming_in_tui),
    ("take_remote_image_urls", _chatwidget_take_remote_image_urls),
    ("refresh_skills_for_current_cwd", _chatwidget_refresh_skills_for_current_cwd),
    ("append_message_history_entry", _chatwidget_append_message_history_entry),
    ("on_list_skills", _chatwidget_on_list_skills),
    ("handle_event", chatwidget_handle_event),
    ("handle_codex_event", chatwidget_handle_codex_event),
]

for name, fn in _helper_names:
    if isinstance(fn, staticmethod):
        setattr(ChatWidget, name, fn)
    else:
        setattr(ChatWidget, name, fn)


__all__ = [
    "ActiveCellTranscriptKey",
    "ActiveCellTranscriptKeyKind",
    "ChatWidget",
    "ChatWidgetInit",
    "CodexOpTarget",
    "ExternalEditorState",
    "ExternalEditorStateKind",
    "InterruptedTurnNoticeMode",
    "PendingSteer",
    "PlanModeNudgeScope",
    "ReplayKind",
    "SessionConfiguredDisplay",
    "ThreadItemRenderSource",
    "ThreadItemRenderSourceKind",
    "TurnAbortReason",
    "UserMessage",
    "contains_plan_keyword",
    "exec_approval_request_from_params",
    "extract_first_bold",
    "has_websocket_timing_metrics",
    "init_chatwidget_full_state",
    "patch_approval_request_from_params",
    "request_permissions_from_params",
    "token_usage_info_from_app_server",
]
