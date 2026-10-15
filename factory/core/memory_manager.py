from __future__ import annotations

from typing import Any

from .builtin_memory_provider import BuiltinMemoryProvider
from .memory_provider import MemoryProvider


class MemoryManager:
    def __init__(self, providers: list[MemoryProvider] | None = None) -> None:
        self._providers = list(providers or [BuiltinMemoryProvider()])

    @property
    def providers(self) -> list[MemoryProvider]:
        return list(self._providers)

    def foundation_context(self, *, data_dir: str | None = None) -> str:
        sections = [provider.foundation_context(data_dir=data_dir).strip() for provider in self._providers]
        return "\n\n".join(section for section in sections if section)

    def handle_explicit_user_memory(
        self,
        user_text: str,
        *,
        data_dir: str | None = None,
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        for provider in self._providers:
            handled, assistant_text, writes = provider.handle_explicit_user_memory(user_text, data_dir=data_dir)
            if handled:
                return True, assistant_text, writes
        return False, None, []

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
        persisted: list[dict[str, Any]] = []
        for provider in self._providers:
            persisted.extend(
                provider.review_turn(
                    user_text=user_text,
                    assistant_text=assistant_text,
                    profile=profile,
                    data_dir=data_dir,
                    legacy_raw_text=legacy_raw_text,
                    log_fn=log_fn,
                )
            )
        return persisted

    def format_memory_update_message(self, persisted_writes: list[dict[str, Any]]) -> str | None:
        for provider in self._providers:
            message = provider.format_memory_update_message(persisted_writes)
            if message:
                return message
        return None
