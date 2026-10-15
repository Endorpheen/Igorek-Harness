import unittest
from factory.core.tui.streaming.mod import StreamState
from factory.core.tui.streaming.controller import StreamController
from factory.core.tui.streaming.chunking import AdaptiveChunkingPolicy, ChunkingMode

class StreamingTest(unittest.TestCase):
    def test_stream_newline_split(self):
        s = StreamState()
        s.feed("hello\nworld")
        lines = s.commit_newlines()
        self.assertEqual(lines, ["hello"])

    def test_stream_multiple_newlines(self):
        s = StreamState()
        s.feed("a\nb\nc")
        lines = s.commit_newlines()
        self.assertEqual(lines, ["a", "b"])

    def test_stream_partial(self):
        s = StreamState()
        s.feed("partial")
        lines = s.commit_newlines()
        self.assertEqual(lines, [])

    def test_stream_drain(self):
        s = StreamState()
        s.feed("hello")
        s.feed("world")
        lines = s.drain_all()
        self.assertEqual(lines, ["helloworld"])

    def test_controller_feed(self):
        c = StreamController()
        c.feed("line1\nline2")
        lines = c.on_commit_tick()
        self.assertEqual(lines, ["line1"])

    def test_controller_tick_combines_queued_partial_chunks(self):
        c = StreamController()
        c.feed("line")
        c.feed("1\n")

        self.assertEqual(c.on_commit_tick(), ["line1"])

    def test_controller_finalize(self):
        c = StreamController()
        c.feed("unfinished")
        lines = c.finalize()
        self.assertEqual(lines, ["unfinished"])

    def test_controller_keeps_partial_tail_mutable(self):
        c = StreamController()
        c.feed("stable\npartial")

        self.assertEqual(c.on_commit_tick_batch(), ["stable"])
        self.assertEqual(c.tail_text(), "partial")

    def test_controller_holds_pipe_table_until_following_line(self):
        c = StreamController()
        c.feed("| A | B |\n|---|---|\n| one | two |\n")

        self.assertEqual(c.on_commit_tick_batch(), [])
        self.assertIn("| one | two |", c.tail_text())

        c.feed("after table\n")
        self.assertEqual(c.on_commit_tick_batch(), [
            "| A | B |",
            "|---|---|",
            "| one | two |",
            "after table",
        ])

    def test_controller_holds_fenced_block_until_closing_fence(self):
        c = StreamController()
        c.feed("```py\nprint('x')\n")

        self.assertEqual(c.on_commit_tick_batch(), [])
        self.assertIn("print('x')", c.tail_text())

        c.feed("```\n")
        self.assertEqual(c.on_commit_tick_batch(), ["```py", "print('x')", "```"])

    def test_chunking_smooth(self):
        p = AdaptiveChunkingPolicy()
        mode = p.decide(1, 0.01)
        self.assertEqual(mode, ChunkingMode.SMOOTH)

    def test_chunking_catch_up(self):
        p = AdaptiveChunkingPolicy()
        mode = p.decide(10, 0.01)
        self.assertEqual(mode, ChunkingMode.CATCH_UP)

    def test_chunking_catch_up_age(self):
        p = AdaptiveChunkingPolicy()
        mode = p.decide(3, 0.2)
        self.assertEqual(mode, ChunkingMode.CATCH_UP)
