from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def foundation_context(self, *, data_dir: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def handle_explicit_user_memory(
        self,
        user_text: str,
        *,
        data_dir: str | None = None,
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def format_memory_update_message(self, persisted_writes: list[dict[str, Any]]) -> str | None:
        raise NotImplementedError
