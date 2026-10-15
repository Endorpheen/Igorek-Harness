package ru.igorek.remote.markdown

import org.junit.Assert.assertEquals
import org.junit.Test
import ru.igorek.remote.ui.markdown.MarkdownParser
import ru.igorek.remote.ui.markdown.MdBlock
import ru.igorek.remote.ui.markdown.codeCopyText

class MarkdownCopyTest {

    private fun codeBlock(markdown: String): MdBlock.Code =
        MarkdownParser.parse(markdown).filterIsInstance<MdBlock.Code>().single()

    @Test
    fun `copy text is the raw code without fences or highlighting artifacts`() {
        val markdown = "```python\ndef greet(name):\n    # note\n    return \"hi \" + name\n```"

        assertEquals(
            "def greet(name):\n    # note\n    return \"hi \" + name",
            codeCopyText(codeBlock(markdown)),
        )
    }

    @Test
    fun `copy text keeps inline backticks and trailing spaces`() {
        val raw = "val query = `select *`  \nprintln(query)"
        val markdown = "```kotlin\n$raw\n```"

        assertEquals(raw, codeCopyText(codeBlock(markdown)))
    }

    @Test
    fun `copy text works for a block without a language label`() {
        val markdown = "```\nplain text\nsecond line\n```"

        assertEquals("plain text\nsecond line", codeCopyText(codeBlock(markdown)))
    }

    @Test
    fun `copy text is never truncated for long blocks`() {
        val lines = (1..500).map { "line $it" }
        val markdown = "```\n" + lines.joinToString("\n") + "\n```"
        val copied = codeCopyText(codeBlock(markdown))

        assertEquals(lines.joinToString("\n"), copied)
        assertEquals(500, copied.lines().size)
    }
}
