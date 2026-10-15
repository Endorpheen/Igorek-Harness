"""Request user input form — structured input overlay for CC-backend requests.

Port of Codex bottom_pane/request_user_input/ with simplified Python implementation.
Supports: text, secret (masked), select (single choice), multiselect (multiple choices).
"""
from __future__ import annotations
from typing import Optional, Callable
from dataclasses import dataclass, field
from .scroll_state import ScrollState


@dataclass
class UserInputOption:
    """Single selectable option."""
    label: str
    description: str = ""


@dataclass
class UserInputQuestion:
    """One question in a structured input request."""
    id: str
    header: str
    question: str
    options: Optional[list[UserInputOption]] = None
    is_secret: bool = False
    is_other: bool = False
    is_multiselect: bool = False
    
    @property
    def field_type(self) -> str:
        """Determine field type from question structure."""
        if self.options is None:
            return "secret" if self.is_secret else "text"
        if self.is_multiselect:
            return "multiselect"
        return "select"


@dataclass
class UserInputRequest:
    """Structured input request from backend."""
    item_id: str
    thread_id: str
    turn_id: str
    questions: list[UserInputQuestion] = field(default_factory=list)


@dataclass
class UserInputAnswer:
    """Answer for a single question."""
    question_id: str
    selected_options: list[str] = field(default_factory=list)
    text_value: str = ""
    notes: str = ""


@dataclass
class UserInputResponse:
    """Complete response with all answers."""
    answers: dict[str, UserInputAnswer] = field(default_factory=dict)


class RequestUserInputForm:
    """Structured input form overlay for request_user_input events.

    Renders a form with multiple fields (text/secret/select) and handles
    navigation, validation, and structured result submission.
    """
    
    def __init__(self, request: UserInputRequest):
        self.request = request
        self.answers: dict[str, UserInputAnswer] = {}
        self.current_field_idx: int = 0
        self.done: bool = False
        self.cancelled: bool = False
        
        # Initialize answers for each question
        for q in request.questions:
            self.answers[q.id] = UserInputAnswer(question_id=q.id)
        
        # Scroll state for select/multiselect options
        self.option_scroll = ScrollState(0, 8)
        if request.questions:
            first_q = request.questions[0]
            if first_q.options:
                self.option_scroll.total = len(first_q.options)
        
        self._width: int = 80
    
    def set_width(self, w: int):
        self._width = w
    
    @property
    def current_question(self) -> Optional[UserInputQuestion]:
        if 0 <= self.current_field_idx < len(self.request.questions):
            return self.request.questions[self.current_field_idx]
        return None
    
    @property
    def current_answer(self) -> Optional[UserInputAnswer]:
        q = self.current_question
        if q:
            return self.answers[q.id]
        return None
    
    def next_field(self):
        """Move to next field."""
        if self.current_field_idx < len(self.request.questions) - 1:
            self.current_field_idx += 1
            self._update_option_scroll()
    
    def prev_field(self):
        """Move to previous field."""
        if self.current_field_idx > 0:
            self.current_field_idx -= 1
            self._update_option_scroll()
    
    def _update_option_scroll(self):
        """Update option scroll state for current field."""
        q = self.current_question
        if q and q.options:
            self.option_scroll.total = len(q.options)
            self.option_scroll.selected = 0
        else:
            self.option_scroll.total = 0
            self.option_scroll.selected = 0
    
    def select_option(self):
        """Select current option for select/multiselect field."""
        q = self.current_question
        ans = self.current_answer
        if not q or not ans or not q.options:
            return
        
        idx = self.option_scroll.selected
        if 0 <= idx < len(q.options):
            option_label = q.options[idx].label
            if q.field_type == "select":
                # Single select: replace selection
                ans.selected_options = [option_label]
            else:
                # Multiselect: toggle
                if option_label in ans.selected_options:
                    ans.selected_options.remove(option_label)
                else:
                    ans.selected_options.append(option_label)
    
    def set_text_value(self, text: str):
        """Set text value for text/secret field."""
        ans = self.current_answer
        if ans:
            ans.text_value = text
    
    def set_notes(self, notes: str):
        """Set notes for current field."""
        ans = self.current_answer
        if ans:
            ans.notes = notes
    
    def submit(self) -> Optional[UserInputResponse]:
        """Submit all answers. Returns response or None if validation fails."""
        # TODO: Add validation if needed
        response = UserInputResponse(answers=self.answers.copy())
        self.done = True
        return response
    
    def cancel(self):
        """Cancel the form."""
        self.cancelled = True
        self.done = True
    
    def render_line_specs(self) -> list[tuple[str, Optional[tuple]]]:
        """Render form as ``(text, action)`` lines.

        ``action`` is ``None`` for plain lines and ``("option", q_idx, opt_idx)``
        for clickable option rows. Used both for text rendering and for the
        mouse-enabled interactive prompt.
        """
        specs: list[tuple[str, Optional[tuple]]] = []

        if not self.request.questions:
            return [("  [No questions in request]", None)]

        specs.append((f"  ┌─ Request {self.request.item_id} ─┐", None))
        specs.append(("", None))

        for idx, q in enumerate(self.request.questions):
            is_current = (idx == self.current_field_idx)
            prefix = "►" if is_current else " "

            specs.append((f"  {prefix} [{q.header}] {q.question}", None))

            ans = self.answers[q.id]
            if q.field_type in ("text", "secret"):
                if q.field_type == "secret" and ans.text_value:
                    display_value = "•" * len(ans.text_value)
                else:
                    display_value = ans.text_value or "<empty>"
                specs.append((f"      Value: {display_value}", None))

            elif q.field_type in ("select", "multiselect") and q.options:
                for opt_idx, opt in enumerate(q.options):
                    opt_prefix = "▶" if (is_current and opt_idx == self.option_scroll.selected) else " "
                    if q.field_type == "multiselect":
                        check = "[x]" if opt.label in ans.selected_options else "[ ]"
                    else:
                        check = "●" if opt.label in ans.selected_options else "○"
                    desc = f" — {opt.description}" if opt.description else ""
                    specs.append(
                        (
                            f"      {opt_prefix} {check} {opt.label}{desc}",
                            ("option", idx, opt_idx),
                        )
                    )

            if ans.notes:
                specs.append((f"      Notes: {ans.notes}", None))

            specs.append(("", None))

        has_multiselect = any(q.is_multiselect for q in self.request.questions)
        if has_multiselect:
            specs.append((
                "  └─ Tab/Shift+Tab: navigate | Space: toggle | Enter: toggle | Ctrl+S: submit | Esc: cancel ─┘",
                None,
            ))
        else:
            specs.append((
                "  └─ Tab/Shift+Tab: navigate | Enter: submit | Ctrl+S: submit | Esc: cancel ─┘",
                None,
            ))

        return specs

    def render_lines(self) -> list[str]:
        """Render form as list of terminal lines."""
        return [text for text, _action in self.render_line_specs()]

    def click_option(self, question_idx: int, option_idx: int) -> None:
        """Select option ``option_idx`` of question ``question_idx`` by mouse."""
        if not (0 <= question_idx < len(self.request.questions)):
            return
        self.current_field_idx = question_idx
        q = self.current_question
        if not q or not q.options:
            return
        self.option_scroll.total = len(q.options)
        self.option_scroll.selected = max(0, min(option_idx, len(q.options) - 1))
        self.option_scroll._ensure_visible()
        self.select_option()
    
    def handle_key(self, key: str) -> Optional[str]:
        """Handle key press. Returns 'submit' or 'cancel' if form is done."""
        if key in ("escape", "c-c"):
            self.cancel()
            return "cancel"

        if key == "c-s":
            result = self.submit()
            if result:
                return "submit"
        
        if key == "tab":
            self.next_field()
        
        elif key in ("s-tab", "backtab"):
            self.prev_field()
        
        elif key == "enter":
            q = self.current_question
            if q and q.field_type in ("select", "multiselect"):
                self.select_option()
            else:
                # Try to submit (for text/secret fields)
                result = self.submit()
                if result:
                    return "submit"
        
        elif key in ("up", "k"):
            if self.option_scroll.total > 0:
                self.option_scroll.move(-1)
        
        elif key in ("down", "j"):
            if self.option_scroll.total > 0:
                self.option_scroll.move(1)
        
        elif key == " ":
            # Space toggles options for select/multiselect
            q = self.current_question
            if q and q.field_type in ("select", "multiselect"):
                self.select_option()
        
        return None
