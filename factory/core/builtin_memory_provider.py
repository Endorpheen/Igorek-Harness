from __future__ import annotations

from typing import Any

from .agent_foundation import extract_explicit_memory_write_request, foundation_prompt_context
from .memory_policy import persist_memory_write
from .memory_provider import MemoryProvider
from .memory_review import review_and_persist_memory


class BuiltinMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "builtin"

    def foundation_context(self, *, data_dir: str | None = None) -> str:
        return foundation_prompt_context(data_dir)

    def handle_explicit_user_memory(
        self,
        user_text: str,
        *,
        data_dir: str | None = None,
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        if str(user_text or "").strip().lower() == "/remember":
            return True, "Использование: /remember <факт>.", []
        explicit_memory = extract_explicit_memory_write_request(user_text)
        if explicit_memory is None:
            return False, None, []

        scope, fact = explicit_memory
        result = persist_memory_write(fact, data_dir=data_dir, scope=scope, source="user", explicit=True)
        if result.get("saved"):
            assistant_text = f"Запомнил и записал это в память ({scope}): {result['fact']}."
        elif result.get("rejected"):
            assistant_text = f"Не стал записывать это в память: {result.get('reason') or result['fact']}."
        else:
            assistant_text = f"Эта запись уже была в памяти: {result['fact']}."
        return True, assistant_text, [result]

    def review_turn(
        self,
        *,
        user_text: str,
        assistant_text: str,
        profile: str | None = None,
        data_dir: str | None = None,
        legacy_raw_text: str | None = None,
        log_fn: Any | None = None,
    ) -> list[dict[str, Any]]:
        return review_and_persist_memory(
            user_text=user_text,
            assistant_text=assistant_text,
            profile=profile,
            data_dir=data_dir,
            legacy_raw_text=legacy_raw_text,
            log_fn=log_fn,
        )

    def format_memory_update_message(self, persisted_writes: list[dict[str, Any]]) -> str | None:
        visible_writes = [item for item in persisted_writes if not item.get("rejected")]
        if not visible_writes:
            return None
        memory_lines: list[str] = []
        for item in visible_writes:
            action = str(item.get("action") or item.get("review_action") or "").strip().lower()
            target = str(item.get("target") or item.get("fact") or "").strip()
            if item.get("changed") and action == "forget":
                memory_lines.append(f"- удалено из Saved facts: {target}")
            elif item.get("changed") and action == "replace":
                memory_lines.append(f"- исправлено в Saved facts: {target} → {item.get('fact', '')}")
            elif item.get("saved"):
                memory_lines.append(f"- сохранён ({item.get('scope')}): {item.get('fact')}")
            else:
                memory_lines.append(f"- уже был в памяти ({item.get('scope')}): {item.get('fact')}")
        return "Память обновлена:\n" + "\n".join(memory_lines)
