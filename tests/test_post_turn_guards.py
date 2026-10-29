import tempfile
import unittest
from pathlib import Path

from factory.core.post_turn_guards import (
    APPROVAL_REQUIRED,
    COMPLETION_ALLOWED,
    REASON_ASK_WITHOUT_CONTENT,
    REASON_FILE_CONTENT_IN_TEXT,
    REASON_PENDING_WRITE,
    RETRY_REQUIRED,
    evaluate_fact_check_guard,
    evaluate_text_completion_guards,
)


class PostTurnGuardsTest(unittest.TestCase):
    def test_text_completion_allows_normal_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = evaluate_text_completion_guards(
                user_message="Ответь кратко.",
                assistant_text="Готово, всё в порядке.",
                workdir=tmp,
            )

        self.assertEqual(verdict.action, COMPLETION_ALLOWED)
        self.assertTrue(verdict.allowed)

    def test_text_completion_requires_approval_for_structural_file_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "diagram.excalidraw"
            verdict = evaluate_text_completion_guards(
                user_message=f"Создай файл {target}",
                assistant_text='{"type":"excalidraw","elements":[]}',
                workdir=tmp,
            )

        self.assertEqual(verdict.action, APPROVAL_REQUIRED)
        self.assertEqual(verdict.reason, REASON_PENDING_WRITE)
        self.assertEqual(verdict.target, str(target))
        self.assertIsNotNone(verdict.pending_write)

    def test_text_completion_allows_read_only_file_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = evaluate_text_completion_guards(
                user_message=(
                    "Проведи обзор проекта без изменения файлов. "
                    "Найди pyproject.toml и определи текущую версию проекта."
                ),
                assistant_text=(
                    "## Обзор проекта\n\n"
                    "Версия `0.1.488` прописана в `pyproject.toml`."
                ),
                workdir=tmp,
            )

        self.assertEqual(verdict.action, COMPLETION_ALLOWED)
        self.assertTrue(verdict.allowed)

    def test_text_completion_allows_read_only_explanation_with_quoted_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = evaluate_text_completion_guards(
                user_message=(
                    "Прочитай factory/core/post_turn_guards.py и объясни его. "
                    "Ничего не изменяй."
                ),
                assistant_text=(
                    "Прочитал `factory/core/post_turn_guards.py`. Файл не трогал.\n\n"
                    "Один из случаев guard:\n"
                    "Модель спросила «записать?», но не приложила содержимое."
                ),
                workdir=tmp,
            )

        self.assertEqual(verdict.action, COMPLETION_ALLOWED)
        self.assertTrue(verdict.allowed)

    def test_text_completion_retries_ask_without_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "diagram.excalidraw"
            verdict = evaluate_text_completion_guards(
                user_message=f"Создай файл {target}",
                assistant_text=f"Могу создать {target}. Подтвердить запись файла?",
                workdir=tmp,
            )

        self.assertEqual(verdict.action, RETRY_REQUIRED)
        self.assertEqual(verdict.reason, REASON_ASK_WITHOUT_CONTENT)
        self.assertEqual(verdict.target, str(target))
        self.assertIn("не включил контент", verdict.retry_message or "")

    def test_text_completion_retries_ask_without_content_even_if_target_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report_builder.py"
            target.write_text("print('old')\n", encoding="utf-8")
            verdict = evaluate_text_completion_guards(
                user_message=f"Создай файл {target}",
                assistant_text=f"Записать файл `{target}` — разрешаешь?",
                workdir=tmp,
            )

        self.assertEqual(verdict.action, RETRY_REQUIRED)
        self.assertEqual(verdict.reason, REASON_ASK_WITHOUT_CONTENT)
        self.assertEqual(verdict.target, str(target))

    def test_text_completion_allows_path_mention_after_real_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "text.py"
            verdict = evaluate_text_completion_guards(
                user_message=f"Создай файл {target}",
                assistant_text=f"Готово, файл {target} записан с print('Hello, world!').",
                workdir=tmp,
                called_tool_names={"Write"},
                called_write_paths={str(target)},
            )

        self.assertEqual(verdict.action, COMPLETION_ALLOWED)
        self.assertTrue(verdict.allowed)

    def test_text_completion_still_retries_path_mention_after_write_to_other_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "text.py"
            other = Path(tmp) / "other.py"
            verdict = evaluate_text_completion_guards(
                user_message=f"Создай файл {target}",
                assistant_text=f"Записать файл {target}?",
                workdir=tmp,
                called_tool_names={"Write"},
                called_write_paths={str(other)},
            )

        self.assertEqual(verdict.action, RETRY_REQUIRED)
        self.assertEqual(verdict.reason, REASON_ASK_WITHOUT_CONTENT)
        self.assertEqual(verdict.target, str(target))

    def test_text_completion_retries_file_content_in_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "diagram.excalidraw"
            verdict = evaluate_text_completion_guards(
                user_message=f"Создай файл {target}",
                assistant_text=(
                    f"Файл {target}\n"
                    '{"type":"excalidraw","elements":[{"id":"a"}'
                ),
                workdir=tmp,
            )

        self.assertEqual(verdict.action, RETRY_REQUIRED)
        self.assertEqual(verdict.reason, REASON_FILE_CONTENT_IN_TEXT)
        self.assertEqual(verdict.target, str(target))
        self.assertIn("вместо вызова Write", verdict.retry_message or "")

    def test_legacy_fact_check_abstains_without_bound_claims(self):
        verdict = evaluate_fact_check_guard(
            tool_results=[
                {
                    "type": "tool_result",
                    "tool_name": "Bash",
                    "content": "expected-token\n",
                }
            ],
            model_text="Результат: wrong-token.",
        )

        self.assertEqual(verdict.action, COMPLETION_ALLOWED)
        self.assertEqual(verdict.correction_message, "")


if __name__ == "__main__":
    unittest.main()
