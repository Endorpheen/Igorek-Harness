"""Commit tick — port of Codex streaming/commit_tick.rs."""
from .chunking import AdaptiveChunkingPolicy, ChunkingMode

class CommitTickRunner:
    def __init__(self, controllers: list):
        self.controllers = controllers
        self.policy = AdaptiveChunkingPolicy()

    def tick(self) -> list[str]:
        mode = self.policy.decide(
            sum(len(c.state._queue) for c in self.controllers),
            0
        )
        if mode == ChunkingMode.SMOOTH:
            for c in self.controllers:
                lines = c.on_commit_tick()
                if lines:
                    return lines[:1]
            return []
        else:
            result = []
            for c in self.controllers:
                result.extend(c.on_commit_tick())
            return result
