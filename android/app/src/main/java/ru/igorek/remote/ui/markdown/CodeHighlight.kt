package ru.igorek.remote.ui.markdown

enum class CodeTokenKind { Plain, Keyword, String, Comment, Number, Function, Type }

data class CodeToken(val text: String, val kind: CodeTokenKind)

/**
 * Light code highlighting without external dependencies: a single pass through the line without
 * regular expressions. Contract: concatenation of token texts exactly matches the input,
 * so an unknown language returns a single plain token entirely.
 */
object CodeHighlighter {

    fun normalizeLanguage(raw: String): String? {
        val token = raw.trim().substringBefore(' ').substringBefore('\t').trim().lowercase()
        if (token.isEmpty()) return null
        return CODE_LANGUAGE_ALIASES[token]
    }

    // Caption for the code block header: the language name as marked in the markup, if recognized.
    fun languageLabel(raw: String): String? {
        val token = raw.trim().substringBefore(' ').trim()
        if (token.isEmpty() || normalizeLanguage(raw) == null) return null
        return token
    }

    fun highlight(code: String, language: String): List<CodeToken> {
        if (code.isEmpty()) return emptyList()
        val key = normalizeLanguage(language) ?: return listOf(CodeToken(code, CodeTokenKind.Plain))
        val profile = CODE_LANGUAGES[key] ?: return listOf(CodeToken(code, CodeTokenKind.Plain))
        return scan(code, profile)
    }

    private fun scan(code: String, profile: CodeLanguage): List<CodeToken> {
        val tokens = mutableListOf<CodeToken>()
        val plain = StringBuilder()

        fun flushPlain() {
            if (plain.isNotEmpty()) {
                tokens += CodeToken(plain.toString(), CodeTokenKind.Plain)
                plain.setLength(0)
            }
        }

        fun emit(text: String, kind: CodeTokenKind) {
            flushPlain()
            if (text.isNotEmpty()) tokens += CodeToken(text, kind)
        }

        var index = 0
        while (index < code.length) {
            val lineComment = profile.lineComments.firstOrNull { code.startsWith(it, index) }
            if (lineComment != null) {
                val end = code.indexOf('\n', index).let { if (it < 0) code.length else it }
                emit(code.substring(index, end), CodeTokenKind.Comment)
                index = end
                continue
            }

            val blockComment = profile.blockComments.firstOrNull { code.startsWith(it.first, index) }
            if (blockComment != null) {
                val close = code.indexOf(blockComment.second, index + blockComment.first.length)
                val end = if (close < 0) code.length else close + blockComment.second.length
                emit(code.substring(index, end), CodeTokenKind.Comment)
                index = end
                continue
            }

            val quote = profile.quotes.firstOrNull { code.startsWith(it, index) && hasClosing(code, index, it) }
            if (quote != null) {
                val end = stringEnd(code, index, quote)
                emit(code.substring(index, end), CodeTokenKind.String)
                index = end
                continue
            }

            if (profile.tagMarkup && code[index] == '<') {
                val tag = scanTag(code, index)
                tag.tokens.forEach { emit(it.text, it.kind) }
                index = tag.end
                continue
            }

            val char = code[index]
            if (char.isDigit()) {
                val end = numberEnd(code, index)
                emit(code.substring(index, end), CodeTokenKind.Number)
                index = end
                continue
            }
            if (isIdentifierStart(char)) {
                var end = index + 1
                while (end < code.length && isIdentifierPart(code[end])) end++
                val word = code.substring(index, end)
                emit(word, classify(word, code, end, profile))
                index = end
                continue
            }

            plain.append(char)
            index++
        }
        flushPlain()
        return tokens
    }

    private fun classify(word: String, code: String, end: Int, profile: CodeLanguage): CodeTokenKind {
        val probe = if (profile.caseInsensitive) word.lowercase() else word
        if (probe in profile.keywords) return CodeTokenKind.Keyword
        if (probe in profile.types) return CodeTokenKind.Type
        var index = end
        while (index < code.length && code[index] == ' ') index++
        if (index < code.length && code[index] == '(') return CodeTokenKind.Function
        if (profile.colonKeys && index < code.length && code[index] == ':') return CodeTokenKind.Type
        return CodeTokenKind.Plain
    }

    // A line should not extend across a line break: an unclosed quote remains
    // as plain text; otherwise, a single apostrophe will format the rest of the file.
    private fun hasClosing(code: String, start: Int, quote: String): Boolean {
        var index = start + quote.length
        while (index < code.length) {
            val char = code[index]
            if (char == '\n' && quote.length == 1) return false
            if (char == '\\') {
                if (index + 1 < code.length && code[index + 1] == '\n' && quote.length == 1) return false
                index += 2
                continue
            }
            if (code.startsWith(quote, index)) return true
            index++
        }
        return false
    }

    private fun stringEnd(code: String, start: Int, quote: String): Int {
        var index = start + quote.length
        while (index < code.length) {
            val char = code[index]
            if (char == '\n' && quote.length == 1) return index
            if (char == '\\') {
                index += 2
                continue
            }
            if (code.startsWith(quote, index)) return index + quote.length
            index++
        }
        return code.length
    }

    private fun numberEnd(code: String, start: Int): Int {
        var index = start
        if (code[index] == '0' && index + 1 < code.length) {
            val marker = code[index + 1]
            if (marker == 'x' || marker == 'X' || marker == 'b' || marker == 'B' || marker == 'o' || marker == 'O') {
                index += 2
                while (index < code.length && (code[index].isLetterOrDigit() || code[index] == '_')) index++
                return index
            }
        }
        while (index < code.length && (code[index].isDigit() || code[index] == '_')) index++
        if (index + 1 < code.length && code[index] == '.' && code[index + 1].isDigit()) {
            index++
            while (index < code.length && code[index].isDigit()) index++
        }
        if (index < code.length && (code[index] == 'e' || code[index] == 'E')) {
            var probe = index + 1
            if (probe < code.length && (code[probe] == '+' || code[probe] == '-')) probe++
            if (probe < code.length && code[probe].isDigit()) {
                index = probe
                while (index < code.length && code[index].isDigit()) index++
            }
        }
        while (index < code.length && code[index] in "fFlLuUdDn") index++
        return index
    }

    private data class TagScan(val end: Int, val tokens: List<CodeToken>)

    private fun scanTag(code: String, start: Int): TagScan {
        val end = tagEnd(code, start)
        val tokens = mutableListOf<CodeToken>()
        var index = start
        while (index < end && code[index] in "<?!/") index++
        val nameStart = index
        while (index < end && isNamePart(code[index])) index++
        tokens += if (index > nameStart) {
            CodeToken(code.substring(start, index), CodeTokenKind.Keyword)
        } else {
            CodeToken(code.substring(start, index), CodeTokenKind.Plain)
        }
        while (index < end) {
            val char = code[index]
            when {
                char == '"' || char == '\'' -> {
                    val quote = char.toString()
                    val stop = minOf(stringEnd(code, index, quote), end)
                    tokens += CodeToken(code.substring(index, stop), CodeTokenKind.String)
                    index = stop
                }
                char.isDigit() -> {
                    val stop = minOf(numberEnd(code, index), end)
                    tokens += CodeToken(code.substring(index, stop), CodeTokenKind.Number)
                    index = stop
                }
                isIdentifierStart(char) -> {
                    var stop = index + 1
                    while (stop < end && isNamePart(code[stop])) stop++
                    tokens += CodeToken(code.substring(index, stop), CodeTokenKind.Function)
                    index = stop
                }
                else -> {
                    tokens += CodeToken(char.toString(), CodeTokenKind.Plain)
                    index++
                }
            }
        }
        return TagScan(end, tokens)
    }

    private fun tagEnd(code: String, start: Int): Int {
        var index = start
        var quote: Char? = null
        while (index < code.length) {
            val char = code[index]
            if (quote != null) {
                if (char == quote) quote = null
            } else when (char) {
                '"', '\'' -> quote = char
                '>' -> return index + 1
            }
            index++
        }
        return code.length
    }

    private fun isIdentifierStart(char: Char): Boolean = char.isLetter() || char == '_' || char == '$'

    private fun isIdentifierPart(char: Char): Boolean = char.isLetterOrDigit() || char == '_' || char == '$'

    private fun isNamePart(char: Char): Boolean =
        char.isLetterOrDigit() || char == '_' || char == '-' || char == ':' || char == '.'
}
