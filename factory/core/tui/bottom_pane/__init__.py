"""Bottom pane subsystem — full port of Codex bottom_pane/mod.rs."""
from __future__ import annotations
from typing import Optional
from ..render.renderable import Renderable, FlexRenderable, RenderableItem, Rect
from ..status_indicator_widget import StatusIndicatorWidget
from .chat_composer import ChatComposer, ChatComposerHistory
from .footer import FooterProps, FooterMode, render_footer_from_props, render_footer_with_context
from .pending_input_preview import PendingInputPreview
from .approval_overlay import ApprovalOverlay, ApprovalRequest
from .unified_exec_footer import UnifiedExecFooter
from .pending_thread_approvals import PendingThreadApprovals

class BottomPane(Renderable):
    """Codex BottomPane: owns composer, status, previews, and footer.

    Layout (Codex lines 1120-1165):
      [status indicator] (flex=0)
      [pending input preview] (flex=1)
      [chat composer] (flex=0)
      [footer] (flex=0)
    """

    def __init__(self):
        self.composer = ChatComposer()
        self.status: Optional[StatusIndicatorWidget] = None
        self.pending_preview = PendingInputPreview()
        self.unified_exec = UnifiedExecFooter()
        self.thread_approvals = PendingThreadApprovals()
        self.approval = ApprovalOverlay()
        self.footer_props = FooterProps()
        self.has_input_focus = True

    def set_status(self, widget: StatusIndicatorWidget):
        self.status = widget

    def build_flex_layout(self) -> FlexRenderable:
        """Codex line 1120: build bottom pane flex layout."""
        flex = FlexRenderable()

        # Status indicator row
        if self.status:
            class _StatusRenderable(Renderable):
                def __init__(self, s): self._s = s
                def render(self, area, buf):
                    for line in self._s.render().splitlines():
                        buf.append(line)
                def desired_height(self, w):
                    return 2  # status line + optional detail
            flex.push(0, RenderableItem(_StatusRenderable(self.status)))

        # Unified exec footer (only if no status widget showing)
        if not self.status and not self.unified_exec.is_empty():
            class _ExecRenderable(Renderable):
                def __init__(self, u): self._u = u
                def render(self, area, buf):
                    buf.append(f"  {self._u.status_text()}")
                def desired_height(self, w): return 1
            flex.push(0, RenderableItem(_ExecRenderable(self.unified_exec)))

        # Pending input preview
        if not self.pending_preview.is_empty():
            class _PreviewRenderable(Renderable):
                def __init__(self, p): self._p = p
                def render(self, area, buf):
                    for line in self._p.render_lines():
                        buf.append(line)
                def desired_height(self, w):
                    return len(self._p.render_lines())
            flex.push(1, RenderableItem(_PreviewRenderable(self.pending_preview)))

        # Composer
        flex.push(0, RenderableItem(self.composer))

        # Footer
        class _FooterRenderable(Renderable):
            def __init__(self, props, w=80): self.props = props; self._w = w
            def render(self, area, buf):
                for line in render_footer_with_context(self.props, self._w):
                    buf.append(line)
            def desired_height(self, w):
                self._w = w
                lines = render_footer_with_context(self.props, w)
                return max(1, len(lines))
        flex.push(0, RenderableItem(_FooterRenderable(self.footer_props)))

        return flex

    def render(self, area: Rect, buf):
        self.build_flex_layout().render(area, buf)

    def desired_height(self, width: int) -> int:
        return self.build_flex_layout().desired_height(width)

    def cursor_pos(self, area: Rect):
        return self.composer.cursor_pos(area)

    def update_task_running(self, running: bool):
        self.footer_props.is_task_running = running
        self.composer.footer_props.is_task_running = running

    def set_context_window(self, percent: Optional[int], used_tokens: Optional[int]):
        self.footer_props.context_window_percent = percent
        self.footer_props.context_window_used_tokens = used_tokens
        self.composer.footer_props.context_window_percent = percent
        self.composer.footer_props.context_window_used_tokens = used_tokens

    def set_status_line(self, text: Optional[str]):
        self.footer_props.status_line_value = text
        self.footer_props.status_line_enabled = bool(text)
        self.composer.footer_props.status_line_value = text
        self.composer.footer_props.status_line_enabled = bool(text)

    def set_active_agent_label(self, label: Optional[str]):
        self.footer_props.active_agent_label = label
