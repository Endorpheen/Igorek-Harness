"""Commit tick — port of Codex streaming/commit_tick.rs."""
from .chunking import AdaptiveChunkingPolicy, ChunkingMode

class CommitTickRunner:
    def __init__(self, controllers: list):
        self.controllers = controllers
        self.policy = AdaptiveChunkingPolicy()

    def tick(self) -> list[str]:
        mode = self.policy.decide(
            sum(c.state.queue_depth() for c in self.controllers),
            max((c.state.oldest_age() for c in self.controllers), default=0.0)
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
                result.extend(c.on_commit_tick_batch())
            return result
