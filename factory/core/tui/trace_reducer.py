"""Trace events -> ChatWidget state reducer."""
from __future__ import annotations

import os
from typing import Any

from ..trace_taxonomy import CcTraceEvent
from .cells.history_cell import (
    DiffCell,
    ErrorCell,
    MarkdownCell,
    SeparatorCell,
    PlanCell,
    TextCell,
    ToolCallCell,
    ToolCallGroupCell,
)
from .chatwidget import ChatWidget
from .streaming.controller import StreamController


def _normalized_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").strip().splitlines())


def _fix_list_markers(text: str) -> str:
    """Merge consecutive identical list markers."""
    import re
    text = re.sub(r'^(\d+)\.\n\1\. ', r'\1. ', text, flags=re.MULTILINE)
    text = re.sub(r'^(\d+)\. \n\1\. ', r'\1. ', text, flags=re.MULTILINE)
    text = re.sub(r'^(\d+)\. \1\. ', r'\1. ', text, flags=re.MULTILINE)
    return text


def _resolve_edit_start_line(file_path: str, old_string: str) -> int | None:
    """Return the 1-based line number in the file where ``old_string`` starts.

    Used to render Edit diffs with real file line numbers (e.g. ``458``)
    instead of fragment-relative indices. Returns ``None`` for any failure:
    missing/empty ``old_string``, unreadable file, encoding errors, or
    ``old_string`` not found in the file. Errors are swallowed because this
    is a render-side helper; a missing start line should never break the
    diff display.
    """
    if not file_path or not old_string:
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    idx = content.find(old_string)
    if idx < 0:
        return None
    return content.count("\n", 0, idx) + 1


def _find_matching_edit_preview(
    committed_cells: list, file_path: str, old_string: str,
):
    """Return the most recent Edit preview DiffCell for the same edit, if any.

    Used to suppress duplicate previews when the backend re-emits a
    ``TOOL_REQUEST_BATCH`` as part of an approval continuation. Without this
    guard, a second preview is created after the file has already been
    mutated, falling back to fragment-relative line numbers because
    ``_resolve_edit_start_line`` returns ``None`` for the already-mutated
    file.
    """
    from .cells.history_cell import DiffCell
    for cell in reversed(committed_cells):
        if not isinstance(cell, DiffCell):
            continue
        if cell.filename != file_path:
            continue
        if cell.old != old_string:
            continue
        return cell
    return None


def _edit_diff_signature(tool_input: dict[str, Any]) -> tuple[str, str, str, bool]:
    return (
        str(tool_input.get("file_path") or ""),
        str(tool_input.get("old_string") or ""),
        str(tool_input.get("new_string") or ""),
        bool(tool_input.get("replace_all")),
    )


def _tool_status_from_result(event: dict[str, Any]) -> str:
    preview = str(event.get("raw_result_preview") or "").lower()
    if preview.startswith("error") or "command timed out" in preview:
        return "error"
    if "(exit code:" in preview:
        return "exit"
    return "done"


def _duration_seconds(event: dict[str, Any]) -> float:
    value = event.get("duration_ms")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value) / 1000.0
    return 0.0


class TraceEventReducer:
    def __init__(
        self,
        widget: ChatWidget | None = None,
        *,
        shown_edit_diff_signatures: set[tuple[str, str, str, bool]] | None = None,
    ):
        self.widget = widget or ChatWidget()
        self.shown_edit_diff_signatures = shown_edit_diff_signatures
        if self.shown_edit_diff_signatures is None:
            self.shown_edit_diff_signatures = set()
        self._last_model_text_preview = ""
        self._pending_preamble = ""
        self._last_inserted_text = ""
        self._is_streaming = False
        self._was_streaming = False
        self._stream_text = ""
        self._stream_controller: StreamController | None = None
        self._stream_marker_emitted = False
        self._task_finish_applied = False
        self.dirty = False

    def apply_many(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self.apply(event)

    def consume_dirty(self) -> bool:
        was_dirty = self.dirty
        self.dirty = False
        return was_dirty

    def apply(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        if kind in {CcTraceEvent.NETWORK_RETRY, CcTraceEvent.NETWORK_RECOVERED}:
            diagnostic = event.get('diagnostic') or {}
            attempt = diagnostic.get('attempt') or (int(event.get('attempt') or 0)+1)
            text = f"Повтор запроса модели · попытка {attempt}" if kind == CcTraceEvent.NETWORK_RETRY else 'Запрос модели восстановлен'
            self.widget.committed_cells.append(TextCell(text))
            self.dirty = True
            self.widget.request_redraw()
            return
        if kind == CcTraceEvent.MODEL_ROUND_RESPONSE:
            self._flush_completed_exploration()
            text = _normalized_text(str(event.get("text_preview") or ""))
            self._last_model_text_preview = text
            self.widget.handle_reasoning_complete()
            # Streaming deltas are canonical. The final payload is redundant and
            # replacing the active tail here causes a visible full-answer redraw.
            if self._is_streaming:
                return
            else:
                # Prefer text_full (complete) when provider doesn't stream deltas
                # (e.g. anthropic-transport models via _http_post).
                full_text = str(event.get("text_full") or "")
                self._pending_preamble = full_text if full_text else text
            return

        if kind == CcTraceEvent.MODEL_TEXT_DELTA:
            delta = str(event.get("text") or "")
            if delta:
                if not self._is_streaming:
                    self._flush_completed_exploration()
                    # Reasoning precedes the answer in the event stream, but
                    # MODEL_ROUND_RESPONSE (which used to commit it) arrives
                    # after the text deltas. Commit it now so the transcript
                    # order stays "reasoning -> answer" (live and resume).
                    self.widget.handle_reasoning_complete()
                    self._maybe_append_final_separator()
                    self._stream_controller = StreamController()
                    self._stream_marker_emitted = False
                self._is_streaming = True
                self._stream_text += delta
                assert self._stream_controller is not None
                self._stream_controller.feed(delta)
                self._append_stream_lines(self._stream_controller.on_commit_tick_batch())
                tail = self._stream_controller.tail_text()
                self.widget.active_cell = (
                    MarkdownCell(tail, marker=not self._stream_marker_emitted)
                    if tail else None
                )
                self.widget.bump_active_cell_revision()
                self.dirty = True
                self.widget.request_redraw()
            return

        if kind == CcTraceEvent.MODEL_REASONING_DELTA:
            delta = str(event.get("text") or "")
            if delta:
                self._flush_completed_exploration()
                self.widget.handle_reasoning_delta(delta)
                self.dirty = True
            return

        if kind == CcTraceEvent.PLAN_UPDATED:
            self._apply_plan_updated(event)
            return

        if kind == CcTraceEvent.TOOL_REQUEST_BATCH:
            self._apply_tool_request_batch(event)
            return

        if kind == CcTraceEvent.TOOL_RESULT:
            self._apply_tool_result(event)
            return

        if kind == CcTraceEvent.TASK_FINISH:
            self._apply_task_finish(event)
            return

        if kind == CcTraceEvent.TOOL_APPROVAL_REQUIRED:
            # No-op: approval dialog is already shown by the runtime,
            # no need for a duplicate text cell in the output.
            pass

    def _apply_tool_request_batch(self, event: dict[str, Any]) -> None:
        tools = event.get("tools")
        if not isinstance(tools, list):
            return

        self._finalize_streaming_if_active()
        group = ToolCallGroupCell()
        for idx, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            tool_id = str(tool.get("id") or f"{event.get('round', '?')}:{idx}")
            name = str(tool.get("name") or "tool")
            if name == "update_plan":
                continue
            tool_input = tool.get("input") if isinstance(tool.get("input"), dict) else {}
            group.add_call(tool_id, name, tool_input)
        if not group.records:
            return
        active = self.widget.active_cell
        if self._pending_preamble:
            self.widget.flush_active_cell()
            self._flush_pending_preamble()
        elif (
            isinstance(active, ToolCallGroupCell)
            and active.extend_exploration(group)
        ):
            self.widget.bump_active_cell_revision()
            self.widget.had_work_activity = True
            self.dirty = True
            self.widget.request_redraw()
            return
        else:
            self.widget.flush_active_cell()
            self._flush_pending_preamble()
        # Show diff preview for Edit tools BEFORE the approval dialog.
        # Skip when a matching preview already exists in committed_cells: the
        # backend re-emits a TOOL_REQUEST_BATCH as an approval continuation,
        # and after the file is mutated _resolve_edit_start_line returns None,
        # so a second preview would render with fragment-relative numbers.
        for rec in group.records:
            if rec.tool_name == "Edit" and isinstance(rec.tool_input, dict):
                old_str = str(rec.tool_input.get("old_string") or "")
                new_str = str(rec.tool_input.get("new_string") or "")
                file_path = str(rec.tool_input.get("file_path") or "")
                replace_all = bool(rec.tool_input.get("replace_all"))
                diff_signature = _edit_diff_signature(rec.tool_input)
                if (
                    old_str != new_str
                    and diff_signature not in self.shown_edit_diff_signatures
                    and _find_matching_edit_preview(
                    self.widget.committed_cells, file_path, old_str,
                    ) is None
                ):
                    start_line = _resolve_edit_start_line(file_path, old_str)
                    self.widget.committed_cells.append(
                        DiffCell(
                            old_str,
                            new_str,
                            file_path,
                            start_line=start_line,
                            replace_all=replace_all,
                        )
                    )
                    self.shown_edit_diff_signatures.add(diff_signature)
        self.widget.active_cell = group
        self.widget.bump_active_cell_revision()
        self.widget.had_work_activity = True
        self.dirty = True
        self.widget.request_redraw()

    def _apply_plan_updated(self, event: dict[str, Any]) -> None:
        plan = event.get("plan")
        if not isinstance(plan, list):
            return
        explanation = event.get("explanation")
        if explanation is not None and not isinstance(explanation, str):
            explanation = None
        self._finalize_streaming_if_active()
        self.widget.flush_active_cell()
        self._flush_pending_preamble()
        self.widget.handle_plan_update(plan, explanation=explanation)
        self.widget.had_work_activity = True
        self.dirty = True

    def _apply_tool_result(self, event: dict[str, Any]) -> None:
        tool_id = str(event.get("tool_use_id") or "")
        tool_name = str(event.get("tool_name") or "tool")
        if tool_name == "update_plan":
            return
        tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        status = _tool_status_from_result(event)
        elapsed = _duration_seconds(event)
        output = str(event.get("raw_result_preview") or "")

        active = self.widget.active_cell
        if isinstance(active, ToolCallGroupCell) and active.complete_call(
            tool_id,
            status=status,
            elapsed=elapsed,
            output=output,
        ):
            self.widget.bump_active_cell_revision()
            if not active.is_active():
                if active.is_exploration_group():
                    self.widget.request_redraw()
                else:
                    self.widget.flush_active_cell()
        else:
            completed = ToolCallCell(tool_name, tool_input, status=status, elapsed=elapsed, output=output)
            self.widget.committed_cells.append(completed)

        # Edit diffs are rendered only as request-time previews, before the
        # approval dialog. Approval continuations can arrive as a separate task,
        # so result-time matching against the current reducer history is not a
        # reliable duplicate guard. The result should only render the tool
        # status/output line.

        self.widget.had_work_activity = True
        self.dirty = True
        self.widget.request_redraw()

    def _apply_task_finish(self, event: dict[str, Any]) -> None:
        self._task_finish_applied = True
        status = str(event.get("status") or "")
        output = _normalized_text(str(event.get("output_preview") or ""))
        self._finalize_streaming_if_active()
        self.widget.flush_active_cell()
        # Flush any preamble accumulated from non-streaming providers
        # (e.g. anthropic-transport models that send text_full without deltas).
        had_preamble = bool(self._pending_preamble)
        self._flush_pending_preamble()
        if status == "ok":
            # Streaming already provided the text — don't add duplicate
            if self._was_streaming:
                self._was_streaming = False
                self._stream_text = ""
            elif had_preamble:
                # Preamble already committed the full text — skip output_preview
                pass
            elif output and output != self._last_inserted_text:
                self._append_final_markdown(output)
        elif status:
            reason = str(event.get("reason") or "task failed")
            # Don't show error cell for approval_required — approval dialog handles it
            if reason != "approval_required":
                # For http_error prefer the full output_preview (e.g. "HTTP 500: …")
                # over the short reason label, so the user sees the actual
                # provider response in the error cell.
                if reason == "http_error" and output:
                    error_text = output
                else:
                    error_text = reason
                if isinstance(event.get('diagnostic'), dict) and event['diagnostic'].get('code'):
                    from ..diagnostics import CODES, sanitize_observation, safe_text
                    diagnostic = sanitize_observation(event['diagnostic'])
                    error_text = CODES[diagnostic['code']]['ru']+'\n'+safe_text(error_text, 1000)+'\nКод: '+diagnostic['code']
                    if diagnostic.get('provider_request_id'):
                        error_text += '\nЗапрос: '+diagnostic['provider_request_id']
                self.widget.committed_cells.append(ErrorCell(error_text))
                self.dirty = True
        self._was_streaming = False
        self._stream_text = ""
        self.widget.agent_turn_running = False
        self.widget.request_redraw()

    def _flush_pending_preamble(self) -> None:
        if not self._pending_preamble:
            return
        if self._pending_preamble != self._last_inserted_text:
            self._maybe_append_final_separator()
            self._append_markdown(self._pending_preamble)
        self._pending_preamble = ""

    def _flush_completed_exploration(self) -> None:
        active = self.widget.active_cell
        if (
            isinstance(active, ToolCallGroupCell)
            and active.is_exploration_group()
            and not active.is_active()
        ):
            self.widget.flush_active_cell()

    def _finalize_streaming_if_active(self) -> None:
        if not self._is_streaming:
            return
        if self._stream_controller is not None:
            self._append_stream_lines(self._stream_controller.finalize())
        self.widget.active_cell = None
        self.widget.bump_active_cell_revision()
        self._was_streaming = True
        self._is_streaming = False
        self._stream_controller = None
        self._stream_marker_emitted = False
        self._pending_preamble = ""
        self._last_inserted_text = _normalized_text(self._stream_text)
        self.widget.handle_reasoning_complete()

    def _append_stream_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        source = "\n".join(lines)
        if lines[-1] == "":
            source += "\n"
        # Append to last MarkdownCell if it exists and is still streaming
        if (self.widget.committed_cells
                and isinstance(self.widget.committed_cells[-1], MarkdownCell)
                and getattr(self.widget.committed_cells[-1], 'is_stream_continuation', lambda: False)()):
            prev = self.widget.committed_cells[-1]
            prev.text += "\n" + source
            prev.text = _fix_list_markers(prev.text)
        else:
            self.widget.committed_cells.append(
                MarkdownCell(_fix_list_markers(source), marker=not self._stream_marker_emitted)
            )
        self._stream_marker_emitted = True
        self.dirty = True

    def _append_markdown(self, text: str) -> None:
        text = _fix_list_markers(text)
        self.widget.committed_cells.append(MarkdownCell(text, marker=True))
        self._last_inserted_text = text

    def _append_final_markdown(self, text: str) -> None:
        text = _fix_list_markers(text)
        self._maybe_append_final_separator()
        self.widget.committed_cells.append(MarkdownCell(text, marker=True))
        self._last_inserted_text = text
        self.dirty = True

    def _maybe_append_final_separator(self) -> None:
        # Port of Codex ``FinalMessageSeparator``: before the next assistant
        # message after a block of concrete work, emit a full-width dim rule
        # and a blank line, then consume the work-activity flag so the same
        # turn does not get a second divider.
        if not self.widget.had_work_activity:
            return
        self.widget.committed_cells.append(SeparatorCell(full_width=True))
        self.widget.committed_cells.append(TextCell(""))
        self.widget.had_work_activity = False

    def display_lines(self, width: int = 120) -> list[str]:
        return self.widget.history_lines(width)
