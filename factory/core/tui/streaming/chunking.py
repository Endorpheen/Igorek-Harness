"""Adaptive chunking — port of Codex streaming/chunking.rs."""
from enum import Enum
import time

class ChunkingMode(Enum):
    SMOOTH = 1
    CATCH_UP = 2

class AdaptiveChunkingPolicy:
    def __init__(self, backlog_threshold: int = 8, age_threshold: float = 0.12):
        self.mode = ChunkingMode.SMOOTH
        self.backlog_threshold = backlog_threshold
        self.age_threshold = age_threshold

    def decide(self, queue_depth: int, oldest_age: float) -> ChunkingMode:
        if queue_depth >= self.backlog_threshold or oldest_age >= self.age_threshold:
            self.mode = ChunkingMode.CATCH_UP
        elif queue_depth <= 2:
            self.mode = ChunkingMode.SMOOTH
        return self.mode
