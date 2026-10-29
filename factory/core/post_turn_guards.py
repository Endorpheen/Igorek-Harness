"""Post-turn completion guards for cc-backend executor.

This module coordinates specialized deterministic checks.  It does not replace
`output_contracts` or `fact_checker`; it gives the executor one structured
verdict before a turn is allowed to finish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory.core.fact_checker import check_tool_result_fact

from .output_contracts import (
    PendingWriteAction,
    detect_ask_to_write_without_content,
    detect_file_content_in_text,
    detect_pending_write_action,
)


COMPLETION_ALLOWED = "completion_allowed"
APPROVAL_REQUIRED = "approval_required"
RETRY_REQUIRED = "retry_required"
CORRECTION_REQUIRED = "correction_required"

REASON_PENDING_WRITE = "completion_blocked_missing_write_tool"
REASON_ASK_WITHOUT_CONTENT = "completion_blocked_missing_artifact"
REASON_FILE_CONTENT_IN_TEXT = "completion_blocked_textual_file_artifact"
REASON_FACT_CHECK = "completion_blocked_tool_result_contradiction"
REASON_UNFULFILLED_WRITE_CONTRACT = "completion_blocked_write_contract_unfulfilled"


@dataclass(frozen=True)
class TextCompletionGuardVerdict:
    action: str
    reason: str = COMPLETION_ALLOWED
    target: str | None = None
    pending_write: PendingWriteAction | None = None
    retry_message: str | None = None

    @property
    def allowed(self) -> bool:
        return self.action == COMPLETION_ALLOWED


@dataclass(frozen=True)
class FactCheckGuardVerdict:
    action: str
    reason: str = COMPLETION_ALLOWED
    correction_message: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == COMPLETION_ALLOWED


def ask_without_content_retry_message() -> str:
    return (
        "[СЛУЖЕБНОЕ. Это внутренняя инструкция от системы, не комментируй её в ответе пользователю.] "
        "Ты спросил разрешение на запись файла, но не включил контент файла. "
        "Не спрашивай повторно — сразу сгенерируй полный контент и:\n"
        "- либо вызови Write tool_use с file_path и полным content;\n"
        "- либо выведи JSON-артефакт полностью в ответе.\n"
        "Не используй псевдовызовы вроде excalidraw.write_file() или print(Write(...)). "
        "Только настоящий tool_use."
    )


def file_content_in_text_retry_message() -> str:
    return (
        "[СЛУЖЕБНОЕ. Это внутренняя инструкция от системы, не комментируй её в ответе пользователю.] "
        "Ты вывел содержимое файла текстом вместо вызова Write tool_use. "
        "Это неправильно — файл должен быть создан через инструмент Write.\n\n"
        "Вызови Write tool_use с file_path и полным content. "
        "НЕ выводи JSON, код или другие артефакты текстом — только через Write."
    )


def evaluate_text_completion_guards(
    *,
    user_message: str,
    assistant_text: str,
    workdir: str,
    must_write_skills: list[str] | None = None,
    called_tool_names: set[str] | None = None,
    called_write_paths: set[str] | None = None,
) -> TextCompletionGuardVerdict:
    """Return a structured verdict for a text-only model response."""
    called_tool_names = called_tool_names or set()
    called_write_paths = called_write_paths or set()
    pending_write = detect_pending_write_action(
        user_message=user_message,
        assistant_text=assistant_text,
        workdir=workdir,
    )
    if pending_write is not None:
        return TextCompletionGuardVerdict(
            action=APPROVAL_REQUIRED,
            reason=REASON_PENDING_WRITE,
            target=pending_write.file_path,
            pending_write=pending_write,
        )

    ask_path = detect_ask_to_write_without_content(
        user_message=user_message,
        assistant_text=assistant_text,
        workdir=workdir,
    )
    if ask_path is not None and ask_path not in called_write_paths:
        return TextCompletionGuardVerdict(
            action=RETRY_REQUIRED,
            reason=REASON_ASK_WITHOUT_CONTENT,
            target=ask_path,
            retry_message=ask_without_content_retry_message(),
        )

    file_content_path = detect_file_content_in_text(
        user_message=user_message,
        assistant_text=assistant_text,
        workdir=workdir,
    )
    if file_content_path is not None and file_content_path not in called_write_paths:
        return TextCompletionGuardVerdict(
            action=RETRY_REQUIRED,
            reason=REASON_FILE_CONTENT_IN_TEXT,
            target=file_content_path,
            retry_message=file_content_in_text_retry_message(),
        )

    unfulfilled = evaluate_write_contract_guard(
        must_write_skills=must_write_skills,
        called_tool_names=called_tool_names,
        user_message=user_message,
    )
    if unfulfilled is not None:
        return unfulfilled

    return TextCompletionGuardVerdict(action=COMPLETION_ALLOWED)


def write_contract_retry_message(skill_names: list[str]) -> str:
    names = ", ".join(skill_names)
    return (
        f"[СЛУЖЕБНОЕ. Это внутренняя инструкция от системы, не комментируй её в ответе пользователю.] "
        f"Ты использовал скилл(ы) {names}, которые требуют обязательной записи файла "
        f"(must_write: true), но Write tool_use так и не был вызван.\n\n"
        f"Следуй контракту скилла: немедленно вызови Write с file_path и полным content. "
        f"Не выводи текст с объяснениями — просто создай файл через Write."
    )


def evaluate_write_contract_guard(
    *,
    must_write_skills: list[str] | None,
    called_tool_names: set[str],
    user_message: str,
) -> TextCompletionGuardVerdict | None:
    if not must_write_skills:
        return None
    if "Write" in called_tool_names:
        return None
    return TextCompletionGuardVerdict(
        action=RETRY_REQUIRED,
        reason=REASON_UNFULFILLED_WRITE_CONTRACT,
        target=None,
        retry_message=write_contract_retry_message(must_write_skills),
    )


def evaluate_fact_check_guard(
    *,
    tool_results: list[dict[str, Any]],
    model_text: str,
    log_fn=None,
) -> FactCheckGuardVerdict:
    """Legacy compatibility adapter; unbound prose is not fact-checkable."""
    needs_correction, error_msg = check_tool_result_fact(tool_results, model_text, log_fn)
    if needs_correction:
        return FactCheckGuardVerdict(
            action=CORRECTION_REQUIRED,
            reason=REASON_FACT_CHECK,
            correction_message=error_msg,
        )
    return FactCheckGuardVerdict(action=COMPLETION_ALLOWED)
