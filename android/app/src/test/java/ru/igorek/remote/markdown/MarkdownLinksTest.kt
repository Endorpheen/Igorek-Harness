package ru.igorek.remote.markdown

import androidx.compose.ui.graphics.Color
import org.junit.Assert.*
import org.junit.Test
import ru.igorek.remote.ui.markdown.*

class MarkdownLinksTest {
    private fun annotated(text: String) = annotate(MarkdownParser.parseInline(text), Color.Gray, Color.White, Color.Green)

    @Test
    fun `markdown links preserve label exact target and utf16 offsets`() {
        val text = annotated("🎵 [Первый](https://example.org/a?q=1#part) и [Второй](https://example.net/b)")
        assertEquals("🎵 Первый и Второй", text.text)
        val links = text.getStringAnnotations(WEB_LINK_TAG, 0, text.length)
        assertEquals(2, links.size)
        assertEquals("https://example.org/a?q=1#part", links[0].item)
        assertEquals("Первый", text.text.substring(links[0].start, links[0].end))
        assertEquals("https://example.net/b", links[1].item)
        assertTrue(text.getStringAnnotations(WEB_LINK_TAG, 0, 1).isEmpty())
    }

    @Test
    fun `bare urls retain underscores punctuation and balanced parentheses`() {
        val raw = "Источники: https://example.org/my_post_two?q=a_b, https://en.wikipedia.org/wiki/Foo_(bar))."
        val links = MarkdownParser.parseInline(raw).filterIsInstance<MdSpan.Link>()
        assertEquals(listOf("https://example.org/my_post_two?q=a_b", "https://en.wikipedia.org/wiki/Foo_(bar)"), links.map { it.url })
        assertEquals(raw, MarkdownParser.parseInline(raw).joinToString("") { it.text })
        assertEquals(2, annotated(raw).getStringAnnotations(WEB_LINK_TAG, 0, raw.length).size)
    }

    @Test
    fun `markdown destination with parentheses remains intact`() {
        val link = MarkdownParser.parseInline("[Статья](https://example.org/Foo_(bar))").filterIsInstance<MdSpan.Link>().single()
        assertEquals("https://example.org/Foo_(bar)", link.url)
        assertEquals(link.url, annotated("[Статья](${link.url})").getStringAnnotations(WEB_LINK_TAG, 0, 6).single().item)
    }

    @Test
    fun `bullet and numbered prefixes retain link annotation position`() {
        for (marker in listOf("•  ", "12. ")) {
            val text = prefix(marker, Color.Green, annotated("[Источник](https://example.org/source)"))
            val link = text.getStringAnnotations(WEB_LINK_TAG, 0, text.length).single()
            assertEquals(marker.length, link.start)
            assertEquals("Источник", text.text.substring(link.start, link.end))
        }
    }

    @Test
    fun `links in heading table and note body use same parser`() {
        val blocks = MarkdownParser.parse("# [Заголовок](https://example.org/h)\n\n| Источник | URL |\n|---|---|\n| Один | [Открыть](https://example.org/t) |\n\n▸ Итог: [Подробнее](https://example.org/n)")
        val heading = blocks.filterIsInstance<MdBlock.Heading>().single()
        val table = blocks.filterIsInstance<MdBlock.Table>().single()
        val note = blocks.filterIsInstance<MdBlock.Note>().single()
        for (spans in listOf(heading.spans, table.rows[0][1], MarkdownParser.parseInline(note.text))) {
            assertEquals(1, annotate(spans, Color.Gray, Color.White, Color.Green).getStringAnnotations(WEB_LINK_TAG, 0, 100).size)
        }
    }

    @Test
    fun `code retains literal urls without activating commands or links`() {
        val inline = annotated("`https://example.org/code` и `[Открыть](https://example.org/code)`")
        assertTrue(inline.getStringAnnotations(WEB_LINK_TAG, 0, inline.length).isEmpty())
        val block = MarkdownParser.parse("```\nhttps://example.org/code\n```").single() as MdBlock.Code
        assertEquals("https://example.org/code", block.text)
    }

    @Test
    fun `only web schemes with valid hosts become actionable`() {
        for (url in listOf("javascript:alert(1)", "file:///sdcard/report", "intent://example/#Intent;end", "data:text/html,hi", "mailto:me@example.org", "https://", "https://example.org:99999")) {
            assertNull(MarkdownLinks.webUrl(url))
            val text = annotated("[Ссылка]($url)")
            assertTrue(text.getStringAnnotations(WEB_LINK_TAG, 0, text.length).isEmpty())
        }
        assertEquals("https://example.org/path", MarkdownLinks.webUrl(" HTTPS://EXAMPLE.ORG/path "))
        assertEquals("https://xn--e1afmkfd.xn--p1ai/", MarkdownLinks.webUrl("https://пример.рф"))
    }

    @Test
    fun `empty and incomplete streaming answers remain readable`() {
        for (raw in listOf("", "Смотри [ссылку](https://", "https://", "**часть ответа")) {
            val text = annotated(raw)
            assertTrue(text.getStringAnnotations(WEB_LINK_TAG, 0, text.length).isEmpty())
        }
    }

    @Test
    fun `pepe token reaches inline renderer without breaking links or code`() {
        val field = ru.igorek.remote.ui.PepeEmojis::class.java.getDeclaredField("entries")
        field.isAccessible = true
        val previous = field.get(null)
        try {
            val id = "pepe_happy_clap"
            val entry = ru.igorek.remote.ui.PepeEntry(id, "Happy clap", "Хлопает", "pepe/$id.gif")
            field.set(null, mapOf(id to entry))
            val text = annotated("Привет :pepe_happy_clap: [ссылка](https://example.org) `:pepe_happy_clap:`")
            val stickers = text.getStringAnnotations(ru.igorek.remote.ui.PEPE_TAG, 0, text.length)
            assertEquals(1, stickers.size)
            assertEquals(id, stickers.single().item)
            assertEquals(1, text.getStringAnnotations(WEB_LINK_TAG, 0, text.length).size)
        } finally {
            field.set(null, previous)
        }
    }
}
