"""Tests for the Rich CLI shared helpers.

Covers `_strip_unbalanced_inline_backticks` (unpaired backtick cleanup) and
the end-to-end Markdown render of the cleaned-up text.
"""

import re
import unittest

from rich.console import Console
from rich.markdown import Markdown

from factory.core.cli_chat_rich_shared import (
    _strip_unbalanced_inline_backticks,
)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _md_plain(text: str) -> str:
    """Render Markdown text through Rich and strip ANSI codes."""
    import io
    buf = io.StringIO()
    Console(file=buf, width=120, force_terminal=True, color_system=None).print(
        Markdown(text)
    )
    return _ANSI_RE.sub("", buf.getvalue()).rstrip()


class TestStripUnbalancedInlineBackticks(unittest.TestCase):
    def test_valid_inline_pair_untouched(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("valid `code` here"),
            "valid `code` here",
        )

    def test_two_inline_pairs_untouched(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("`a` and `b`"),
            "`a` and `b`",
        )

    def test_bold_with_inline_code_untouched(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("**`think`** вынесен"),
            "**`think`** вынесен",
        )

    def test_plain_bold_untouched(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("plain **bold** text"),
            "plain **bold** text",
        )

    def test_escaped_backtick_untouched(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("escaped \\` not touched"),
            "escaped \\` not touched",
        )

    def test_single_trailing_backtick_stripped(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("слово `"),
            "слово ",
        )

    def test_three_inline_backticks_drop_last(self):
        # Real case from Igorek's screenshot: a lingering 'backtick' (``) after the `block` command.
        self.assertEqual(
            _strip_unbalanced_inline_backticks(
                "переезжают из text в структурный `__reasoning` block`"
            ),
            "переезжают из text в структурный `__reasoning` block",
        )

    def test_single_orphan_backtick_stripped(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("`апострофы` (одинарные `) едятся"),
            "`апострофы` (одинарные ) едятся",
        )

    def test_fenced_code_preserved_verbatim(self):
        src = "```\ncode `inside` block\n```"
        self.assertEqual(_strip_unbalanced_inline_backticks(src), src)

    def test_fenced_with_inline_after_preserved(self):
        # 2 inline + 2 inline = 4 → even, no change.
        src = "```\nfenced\n``` and `pair` ok"
        self.assertEqual(_strip_unbalanced_inline_backticks(src), src)

    def test_multiline_preserves_fenced_only(self):
        src = "intro `ok`\n```\nnot `touched` here\n```\ntrailing `"
        out = _strip_unbalanced_inline_backticks(src)
        # fenced stays as-is, trailing line is fixed.
        self.assertIn("not `touched` here", out)
        self.assertTrue(out.endswith("trailing "))

    def test_five_backticks_inside_bold_even(self):
        # 5 inline → odd → strip last. But here the inner two are escaped
        # `\`...\``, so helper counts 3 inline: first, last, and the literal
        # ones in `**`...`**`. Adjust expectation based on actual layout.
        # Input has 5 raw inline `` ` ``, helper counts 5, odd → strip last.
        src = "`a` b `c` d `e"
        out = _strip_unbalanced_inline_backticks(src)
        self.assertEqual(out, "`a` b `c` d e")

    def test_no_backticks_unchanged(self):
        self.assertEqual(
            _strip_unbalanced_inline_backticks("просто текст без бэктиков"),
            "просто текст без бэктиков",
        )

    def test_input_not_mutated(self):
        src = "висящий `"
        src_copy = src
        _strip_unbalanced_inline_backticks(src)
        self.assertEqual(src, src_copy)


class TestStripUnbalancedRendersCleanly(unittest.TestCase):
    """End-to-end: cleaned text must render with no stray trailing backtick."""

    def test_trailing_stray_disappears_in_output(self):
        out = _md_plain(
            _strip_unbalanced_inline_backticks(
                "переезжают из text в структурный `__reasoning` block`"
            )
        )
        self.assertFalse(
            out.rstrip().endswith("`"),
            msg=f"expected no trailing backtick, got: {out!r}",
        )

    def test_apostrophe_orphan_disappears(self):
        out = _md_plain(
            _strip_unbalanced_inline_backticks(
                "**Апострофы** (одинарные `) едятся"
            )
        )
        self.assertNotIn("(одинарные `)", out)
        self.assertIn("Апострофы", out)

    def test_paired_inline_code_still_renders(self):
        out = _md_plain(
            _strip_unbalanced_inline_backticks("`a` и `b`")
        )
        # Rich strips backticks in plain text output, but `a` and `b` must be
        # present (as inline code content).
        self.assertIn("a", out)
        self.assertIn("b", out)
        self.assertNotIn("`", out)

    def test_fenced_block_still_preserved(self):
        out = _md_plain(
            _strip_unbalanced_inline_backticks(
                "```\nnot `touched` here\n```"
            )
        )
        # Fenced code content must survive unchanged.
        self.assertIn("not `touched` here", out)


if __name__ == "__main__":
    unittest.main()
