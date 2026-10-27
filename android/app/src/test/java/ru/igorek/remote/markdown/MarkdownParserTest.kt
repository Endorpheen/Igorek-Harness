package ru.igorek.remote.markdown

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.igorek.remote.ui.markdown.MarkdownParser
import ru.igorek.remote.ui.markdown.MdBlock
import ru.igorek.remote.ui.markdown.MdSpan

class MarkdownParserTest {

    @Test
    fun `plain paragraph becomes one paragraph block`() {
        val blocks = MarkdownParser.parse("просто текст без разметки")

        assertEquals(1, blocks.size)
        val paragraph = blocks[0] as MdBlock.Paragraph
        assertEquals(listOf("просто текст без разметки"), paragraph.spans.map { it.text })
    }

    @Test
    fun `adjacent lines are joined into one paragraph`() {
        val blocks = MarkdownParser.parse("первая строка\nвторая строка")

        assertEquals(1, blocks.size)
        val paragraph = blocks[0] as MdBlock.Paragraph
        assertEquals("первая строка вторая строка", paragraph.spans.joinToString("") { it.text })
    }

    @Test
    fun `blank line splits paragraphs`() {
        val blocks = MarkdownParser.parse("первый\n\nвторой")

        assertEquals(2, blocks.size)
        assertTrue(blocks.all { it is MdBlock.Paragraph })
    }

    @Test
    fun `headings of three levels are recognised`() {
        val blocks = MarkdownParser.parse("# один\n## два\n### три")

        assertEquals(listOf(1, 2, 3), blocks.map { (it as MdBlock.Heading).level })
    }

    @Test
    fun `bullet list items are separate blocks`() {
        val blocks = MarkdownParser.parse("- первый\n- второй")

        assertEquals(2, blocks.size)
        assertEquals("первый", (blocks[0] as MdBlock.Bullet).spans.joinToString("") { it.text })
        assertEquals("второй", (blocks[1] as MdBlock.Bullet).spans.joinToString("") { it.text })
    }

    @Test
    fun `numbered list keeps its marker`() {
        val blocks = MarkdownParser.parse("1. раз\n2) два")

        assertEquals(listOf("1.", "2)"), blocks.map { (it as MdBlock.Numbered).marker })
    }

    @Test
    fun `fenced code block keeps language and text`() {
        val blocks = MarkdownParser.parse("```bash\nls -la\necho ok\n```")

        assertEquals(1, blocks.size)
        val code = blocks[0] as MdBlock.Code
        assertEquals("bash", code.language)
        assertEquals("ls -la\necho ok", code.text)
    }

    @Test
    fun `code block inside text splits surrounding paragraphs`() {
        val blocks = MarkdownParser.parse("до\n```\ncode\n```\nпосле")

        assertEquals(3, blocks.size)
        assertTrue(blocks[0] is MdBlock.Paragraph)
        assertTrue(blocks[1] is MdBlock.Code)
        assertTrue(blocks[2] is MdBlock.Paragraph)
    }

    @Test
    fun `inline bold italic code and link are parsed`() {
        val spans = MarkdownParser.parseInline("**жирный** и *курсив* и `код` и [ссылка](https://example.com)")

        val bold = spans.filterIsInstance<MdSpan.Bold>().single()
        val italic = spans.filterIsInstance<MdSpan.Italic>().single()
        val code = spans.filterIsInstance<MdSpan.CodeSpan>().single()
        val link = spans.filterIsInstance<MdSpan.Link>().single()
        assertEquals("жирный", bold.text)
        assertEquals("курсив", italic.text)
        assertEquals("код", code.text)
        assertEquals("ссылка", link.text)
        assertEquals("https://example.com", link.url)
    }

    @Test
    fun `single asterisk inside word is not italic marker`() {
        val spans = MarkdownParser.parseInline("2 * 3 = 6")

        assertEquals("2 * 3 = 6", spans.joinToString("") { it.text })
        assertTrue(spans.none { it is MdSpan.Italic })
    }

    @Test
    fun `unclosed markers stay as plain text`() {
        val spans = MarkdownParser.parseInline("**незакрытый жирный")

        assertEquals("**незакрытый жирный", spans.joinToString("") { it.text })
        assertTrue(spans.all { it is MdSpan.Plain })
    }

    @Test
    fun `horizontal rule does not become a paragraph`() {
        val blocks = MarkdownParser.parse("текст\n---\nещё")

        assertEquals(2, blocks.size)
        assertTrue(blocks.all { it is MdBlock.Paragraph })
    }

    @Test
    fun `toPlainText strips formatting`() {
        val plain = MarkdownParser.toPlainText("**жирный** и `код`\n\n```\nстрока кода\n```")

        assertTrue(plain.contains("жирный и код"))
        assertTrue(plain.contains("строка кода"))
        assertTrue(!plain.contains("**"))
    }

    @Test
    fun `empty input produces no blocks`() {
        assertTrue(MarkdownParser.parse("").isEmpty())
        assertTrue(MarkdownParser.parse("   \n  ").isEmpty())
    }

    @Test
    fun `model style answer renders headings bullets and code together`() {
        val answer = """
            ## Итог

            Проверка завершена:

            - первое
            - второе

            ```
            exit code: 0
            ```
        """.trimIndent()

        val blocks = MarkdownParser.parse(answer)

        assertTrue(blocks.any { it is MdBlock.Heading && it.level == 2 })
        assertEquals(2, blocks.count { it is MdBlock.Bullet })
        assertTrue(blocks.any { it is MdBlock.Code })
    }

    @Test
    fun `pipe table becomes table block with header and rows`() {
        val blocks = MarkdownParser.parse(
            """
            | Позиция | Кол-во | Ккал |
            |---|---|---|
            | Харчо | 250 г | ~160 |
            | Филе | 200 г | ~340 |
            """.trimIndent(),
        )

        val table = blocks.filterIsInstance<MdBlock.Table>().single()
        assertEquals(
            listOf("Позиция", "Кол-во", "Ккал"),
            table.header.map { cells -> cells.joinToString("") { it.text } },
        )
        assertEquals(2, table.rows.size)
        assertEquals("Харчо", table.rows[0][0].joinToString("") { it.text })
        assertEquals("~340", table.rows[1][2].joinToString("") { it.text })
    }

    @Test
    fun `table without outer pipes is recognised`() {
        val blocks = MarkdownParser.parse("Дата | Вес\n--- | ---\n2026-09-17 | 106.7")

        val table = blocks.filterIsInstance<MdBlock.Table>().single()
        assertEquals(
            listOf("Дата", "Вес"),
            table.header.map { cells -> cells.joinToString("") { it.text } },
        )
        assertEquals(1, table.rows.size)
    }

    @Test
    fun `header followed by plain text is not a table`() {
        val blocks = MarkdownParser.parse("| просто | текст |\nобычная строка")

        assertTrue(blocks.none { it is MdBlock.Table })
    }

    @Test
    fun `table does not swallow following paragraph`() {
        val blocks = MarkdownParser.parse("| A | B |\n|---|---|\n| 1 | 2 |\n\nпосле таблицы")

        assertTrue(blocks.any { it is MdBlock.Table })
        val tail = blocks.filterIsInstance<MdBlock.Paragraph>().last()
        assertEquals("после таблицы", tail.spans.joinToString("") { it.text })
    }

    @Test
    fun `delimiter row without header is not a table`() {
        val blocks = MarkdownParser.parse("|---|---|")

        assertTrue(blocks.none { it is MdBlock.Table })
    }

    @Test
    fun `memory status line becomes a note plate`() {
        val blocks = MarkdownParser.parse("▸ Память обновлена: Вес Игоря на 2026-09-17 — 106.7 кг")

        val note = blocks.filterIsInstance<MdBlock.Note>().single()
        assertEquals("Память обновлена", note.title)
        assertEquals("Вес Игоря на 2026-09-17 — 106.7 кг", note.text)
    }

    @Test
    fun `toPlainText keeps table rows without delimiter`() {
        val plain = MarkdownParser.toPlainText("| A | B |\n|---|---|\n| 1 | 2 |")

        assertTrue(plain.contains("A | B"))
        assertTrue(plain.contains("1 | 2"))
        assertTrue(!plain.contains("---"))
    }

    @Test
    fun `pepe tokens stay intact next to ordinary formatting`() {
        val spans = MarkdownParser.parseInline("Да, я тут! :pepe_happy_clap: и :pepe_think: _важно_")
        assertEquals("Да, я тут! :pepe_happy_clap: и :pepe_think: важно", spans.joinToString("") { it.text })
        assertEquals(listOf("важно"), spans.filterIsInstance<MdSpan.Italic>().map { it.text })
    }

    @Test
    fun `italic text can contain pepe token without breaking it`() {
        val spans = MarkdownParser.parseInline("_Привет :pepe_happy_clap: друг_")
        assertEquals(listOf(MdSpan.Italic("Привет :pepe_happy_clap: друг")), spans)
    }

    @Test
    fun `pepe tokens in inline code remain literal`() {
        val spans = MarkdownParser.parseInline("`:pepe_happy_clap:` и :pepe_happy_clap:")
        assertEquals(":pepe_happy_clap:", spans.filterIsInstance<MdSpan.CodeSpan>().single().text)
        assertEquals(" и :pepe_happy_clap:", spans.filterIsInstance<MdSpan.Plain>().single().text)
    }

    @Test
    fun `all bundled pepe names survive parsing together`() {
        val ids = listOf("bongo", "think", "happy_clap", "happy_dance", "laugh", "love",
            "give_love", "happy_blanket", "clap", "cry", "sad_rain", "sad_vibes",
            "sleep", "sip_spin", "no_u", "brain_scratch")
        val text = ids.joinToString(" ") { ":pepe_$it:" }
        val spans = MarkdownParser.parseInline(text)
        assertEquals(text, spans.joinToString("") { it.text })
        assertTrue(spans.all { it is MdSpan.Plain })
    }
}
