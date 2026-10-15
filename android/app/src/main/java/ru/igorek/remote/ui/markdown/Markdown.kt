package ru.igorek.remote.ui.markdown

sealed interface MdSpan {
    val text: String

    data class Plain(override val text: String) : MdSpan
    data class Bold(override val text: String) : MdSpan
    data class Italic(override val text: String) : MdSpan
    data class CodeSpan(override val text: String) : MdSpan
    data class Link(override val text: String, val url: String) : MdSpan
}

sealed interface MdBlock {
    data class Paragraph(val spans: List<MdSpan>) : MdBlock
    data class Heading(val level: Int, val spans: List<MdSpan>) : MdBlock
    data class Bullet(val spans: List<MdSpan>) : MdBlock
    data class Numbered(val marker: String, val spans: List<MdSpan>) : MdBlock
    data class Code(val language: String, val text: String) : MdBlock
    data class Note(val title: String, val text: String) : MdBlock
    data class Table(val header: List<List<MdSpan>>, val rows: List<List<List<MdSpan>>>) : MdBlock
}

object MarkdownParser {

    private val NUMBERED = Regex("""^\d+[.)]\s+""")
    private val RULE = Regex("""^(-{3,}|\*{3,}|_{3,})$""")
    private val DELIMITER_CELL = Regex("""^:?-{2,}:?$""")
    private const val NOTE_PREFIX = "▸ "

    fun tableCells(line: String): List<String>? {
        val trimmed = line.trim()
        if (!trimmed.contains('|')) return null
        val inner = trimmed.removePrefix("|").removeSuffix("|")
        val cells = inner.split('|').map { it.trim() }
        if (cells.size < 2) return null
        return cells
    }

    fun isDelimiterRow(line: String): Boolean {
        val cells = tableCells(line) ?: return false
        return cells.all { DELIMITER_CELL.matches(it) }
    }

    fun parse(markdown: String): List<MdBlock> {
        val blocks = mutableListOf<MdBlock>()
        val lines = markdown.replace("\r\n", "\n").split("\n")
        val paragraph = StringBuilder()

        fun flushParagraph() {
            val text = paragraph.toString().trim()
            if (text.isNotEmpty()) blocks += MdBlock.Paragraph(parseInline(text))
            paragraph.setLength(0)
        }

        var index = 0
        while (index < lines.size) {
            val line = lines[index].trimEnd()
            val trimmed = line.trimStart()

            if (trimmed.startsWith("```")) {
                flushParagraph()
                val language = trimmed.removePrefix("```").trim()
                val code = StringBuilder()
                index++
                while (index < lines.size && !lines[index].trimStart().startsWith("```")) {
                    code.append(lines[index]).append('\n')
                    index++
                }
                index++
                blocks += MdBlock.Code(language, code.toString().trimEnd('\n'))
                continue
            }

            val headerCells = tableCells(trimmed)
            if (headerCells != null && index + 1 < lines.size && isDelimiterRow(lines[index + 1])) {
                flushParagraph()
                val rows = mutableListOf<List<List<MdSpan>>>()
                index += 2
                while (index < lines.size) {
                    val rowCells = tableCells(lines[index]) ?: break
                    if (isDelimiterRow(lines[index])) break
                    rows += rowCells.map { parseInline(it) }
                    index++
                }
                blocks += MdBlock.Table(headerCells.map { parseInline(it) }, rows)
                continue
            }

            when {
                trimmed.isEmpty() || RULE.matches(trimmed) -> flushParagraph()
                trimmed.startsWith("### ") -> {
                    flushParagraph()
                    blocks += MdBlock.Heading(3, parseInline(trimmed.removePrefix("### ")))
                }
                trimmed.startsWith("## ") -> {
                    flushParagraph()
                    blocks += MdBlock.Heading(2, parseInline(trimmed.removePrefix("## ")))
                }
                trimmed.startsWith("# ") -> {
                    flushParagraph()
                    blocks += MdBlock.Heading(1, parseInline(trimmed.removePrefix("# ")))
                }
                trimmed.startsWith(NOTE_PREFIX) -> {
                    flushParagraph()
                    val body = trimmed.removePrefix(NOTE_PREFIX).trim()
                    val separator = body.indexOf(": ")
                    if (separator > 0) {
                        blocks += MdBlock.Note(body.take(separator).trim(), body.drop(separator + 2).trim())
                    } else {
                        blocks += MdBlock.Note("", body)
                    }
                }
                trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("• ") -> {
                    flushParagraph()
                    blocks += MdBlock.Bullet(parseInline(trimmed.drop(2)))
                }
                NUMBERED.containsMatchIn(trimmed) -> {
                    flushParagraph()
                    val marker = NUMBERED.find(trimmed)?.value?.trim().orEmpty()
                    blocks += MdBlock.Numbered(marker, parseInline(trimmed.drop(marker.length).trimStart()))
                }
                else -> {
                    if (paragraph.isNotEmpty()) paragraph.append(' ')
                    paragraph.append(trimmed)
                }
            }
            index++
        }
        flushParagraph()
        return blocks
    }

    fun parseInline(text: String): List<MdSpan> {
        val spans = mutableListOf<MdSpan>()
        val plain = StringBuilder()

        fun flushPlain() {
            if (plain.isNotEmpty()) {
                spans += MdSpan.Plain(plain.toString())
                plain.setLength(0)
            }
        }

        var index = 0
        while (index < text.length) {
            val rest = text.substring(index)
            if (rest.startsWith("http", ignoreCase = true)) {
                val range = MarkdownLinks.ranges(rest).firstOrNull()?.takeIf { it.first == 0 }
                if (range != null) {
                    flushPlain()
                    val url = rest.substring(0, range.last + 1)
                    spans += MdSpan.Link(url, url)
                    index += url.length
                    continue
                }
            }
            when {
                rest.startsWith("**") -> {
                    val end = text.indexOf("**", index + 2)
                    if (end > index + 2) {
                        flushPlain()
                        spans += MdSpan.Bold(text.substring(index + 2, end))
                        index = end + 2
                        continue
                    }
                }
                rest.startsWith("`") -> {
                    val end = text.indexOf('`', index + 1)
                    if (end > index + 1) {
                        flushPlain()
                        spans += MdSpan.CodeSpan(text.substring(index + 1, end))
                        index = end + 1
                        continue
                    }
                }
                rest.startsWith("[") -> {
                    val close = text.indexOf(']', index + 1)
                    if (close > index + 1 && close + 1 < text.length && text[close + 1] == '(') {
                        val paren = closingLinkParen(text, close + 1)
                        if (paren > close + 1) {
                            flushPlain()
                            spans += MdSpan.Link(
                                text.substring(index + 1, close),
                                text.substring(close + 2, paren),
                            )
                            index = paren + 1
                            continue
                        }
                    }
                }
                rest.startsWith("*") || rest.startsWith("_") -> {
                    val marker = rest[0]
                    val end = text.indexOf(marker, index + 1)
                    if (end > index + 1) {
                        flushPlain()
                        spans += MdSpan.Italic(text.substring(index + 1, end))
                        index = end + 1
                        continue
                    }
                }
            }
            plain.append(text[index])
            index++
        }
        flushPlain()
        return spans
    }

    private fun closingLinkParen(text: String, opening: Int): Int {
        var depth = 0
        for (index in opening until text.length) {
            when (text[index]) {
                '(' -> depth++
                ')' -> {
                    depth--
                    if (depth == 0) return index
                }
            }
        }
        return -1
    }

    fun toPlainText(markdown: String): String =
        parse(markdown).joinToString("\n") { block ->
            when (block) {
                is MdBlock.Code -> block.text
                is MdBlock.Paragraph -> block.spans.joinToString("") { it.text }
                is MdBlock.Heading -> block.spans.joinToString("") { it.text }
                is MdBlock.Bullet -> "• " + block.spans.joinToString("") { it.text }
                is MdBlock.Numbered -> "${block.marker} " + block.spans.joinToString("") { it.text }
                is MdBlock.Note -> NOTE_PREFIX + listOf(block.title, block.text)
                    .filter { it.isNotEmpty() }
                    .joinToString(": ")
                is MdBlock.Table -> (listOf(block.header) + block.rows).joinToString("\n") { row ->
                    row.joinToString(" | ") { cells -> cells.joinToString("") { it.text } }
                }
            }
        }
}
