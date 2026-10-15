package ru.igorek.remote.markdown

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.igorek.remote.ui.markdown.CodeHighlighter
import ru.igorek.remote.ui.markdown.CodeTokenKind

class CodeHighlightTest {

    private fun token(code: String, language: String, text: String) =
        CodeHighlighter.highlight(code, language).single { it.text == text }

    private fun kindOf(code: String, language: String, text: String) =
        token(code, language, text).kind

    @Test
    fun `python splits keywords strings comments numbers and function names`() {
        val code = "# note\ndef greet(name):\n    size = 42\n    return \"hi \" + name"

        assertEquals(CodeTokenKind.Comment, kindOf(code, "python", "# note"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "python", "def"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "python", "return"))
        assertEquals(CodeTokenKind.Function, kindOf(code, "python", "greet"))
        assertEquals(CodeTokenKind.Number, kindOf(code, "python", "42"))
        assertEquals(CodeTokenKind.String, kindOf(code, "python", "\"hi \""))
    }

    @Test
    fun `kotlin splits keywords strings comments numbers and function names`() {
        val code = "// c\nfun main() {\n    val count = 7\n    println(\"ok\")\n}"

        assertEquals(CodeTokenKind.Comment, kindOf(code, "kotlin", "// c"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "kotlin", "fun"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "kotlin", "val"))
        assertEquals(CodeTokenKind.Function, kindOf(code, "kotlin", "main"))
        assertEquals(CodeTokenKind.Function, kindOf(code, "kotlin", "println"))
        assertEquals(CodeTokenKind.Number, kindOf(code, "kotlin", "7"))
        assertEquals(CodeTokenKind.String, kindOf(code, "kotlin", "\"ok\""))
    }

    @Test
    fun `bash splits keywords strings comments and numbers`() {
        val code = "# c\nif [ -f /tmp/x ]; then\n  echo \"ready\"\n  sleep 2\nfi"

        assertEquals(CodeTokenKind.Comment, kindOf(code, "bash", "# c"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "bash", "if"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "bash", "then"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "bash", "fi"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "bash", "echo"))
        assertEquals(CodeTokenKind.String, kindOf(code, "bash", "\"ready\""))
        assertEquals(CodeTokenKind.Number, kindOf(code, "bash", "2"))
    }

    @Test
    fun `json splits strings numbers and literal keywords`() {
        val code = """{"port": 8080, "ok": true, "name": "igorek"}"""

        assertEquals(CodeTokenKind.String, kindOf(code, "json", "\"port\""))
        assertEquals(CodeTokenKind.Number, kindOf(code, "json", "8080"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "json", "true"))
        assertEquals(CodeTokenKind.String, kindOf(code, "json", "\"igorek\""))
    }

    @Test
    fun `sql keywords are case insensitive`() {
        val code = "-- note\nSELECT id FROM users WHERE age > 18 AND name = 'bob'"

        assertEquals(CodeTokenKind.Comment, kindOf(code, "sql", "-- note"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "sql", "SELECT"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "sql", "FROM"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "sql", "WHERE"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "sql", "AND"))
        assertEquals(CodeTokenKind.String, kindOf(code, "sql", "'bob'"))
        assertEquals(CodeTokenKind.Number, kindOf(code, "sql", "18"))
    }

    @Test
    fun `xml splits tags attributes comments strings and numbers`() {
        val code = "<!-- c -->\n<div class=\"card\">text 7</div>"

        assertEquals(CodeTokenKind.Comment, kindOf(code, "xml", "<!-- c -->"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "xml", "<div"))
        assertEquals(CodeTokenKind.Function, kindOf(code, "xml", "class"))
        assertEquals(CodeTokenKind.String, kindOf(code, "xml", "\"card\""))
        assertEquals(CodeTokenKind.Number, kindOf(code, "xml", "7"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "xml", "</div"))
    }

    @Test
    fun `yaml keys and literals are classified`() {
        val code = "enabled: true # note\nport: 8080\nname: igorek"

        assertEquals(CodeTokenKind.Type, kindOf(code, "yaml", "enabled"))
        assertEquals(CodeTokenKind.Keyword, kindOf(code, "yaml", "true"))
        assertEquals(CodeTokenKind.Comment, kindOf(code, "yaml", "# note"))
        assertEquals(CodeTokenKind.Number, kindOf(code, "yaml", "8080"))
        assertEquals(CodeTokenKind.Type, kindOf(code, "yaml", "port"))
    }

    @Test
    fun `python triple quoted string stays one string token`() {
        val code = "text = \"\"\"line1\nline2\"\"\"\ncount = 1"

        assertEquals(CodeTokenKind.String, kindOf(code, "python", "\"\"\"line1\nline2\"\"\""))
        assertEquals(CodeTokenKind.Number, kindOf(code, "python", "1"))
    }

    @Test
    fun `unterminated quote does not color the rest of the file`() {
        val code = "value = 'broken\nnumber = 5"

        assertEquals(CodeTokenKind.Number, kindOf(code, "python", "5"))
        assertTrue(CodeHighlighter.highlight(code, "python").none { it.kind == CodeTokenKind.String })
    }

    @Test
    fun `unknown or empty language yields a single plain token`() {
        val code = "if (x) return 42 // not highlighted"
        val unknown = CodeHighlighter.highlight(code, "brainfuck")

        assertEquals(1, unknown.size)
        assertEquals(CodeTokenKind.Plain, unknown.single().kind)
        assertEquals(code, unknown.single().text)

        val noLanguage = CodeHighlighter.highlight(code, "")
        assertEquals(1, noLanguage.size)
        assertEquals(CodeTokenKind.Plain, noLanguage.single().kind)
        assertEquals(code, noLanguage.single().text)
    }

    @Test
    fun `empty code produces no tokens`() {
        assertTrue(CodeHighlighter.highlight("", "python").isEmpty())
        assertTrue(CodeHighlighter.highlight("", "unknown").isEmpty())
    }

    @Test
    fun `language aliases normalise and unknown stays null`() {
        assertEquals("python", CodeHighlighter.normalizeLanguage("PY"))
        assertEquals("bash", CodeHighlighter.normalizeLanguage("shell"))
        assertEquals("javascript", CodeHighlighter.normalizeLanguage("JS"))
        assertEquals("typescript", CodeHighlighter.normalizeLanguage("tsx"))
        assertEquals("xml", CodeHighlighter.normalizeLanguage("HTML"))
        assertEquals("cpp", CodeHighlighter.normalizeLanguage("C++"))
        assertEquals("kotlin", CodeHighlighter.normalizeLanguage("kotlin title=App.kt"))
        assertNull(CodeHighlighter.normalizeLanguage("brainfuck"))
        assertNull(CodeHighlighter.normalizeLanguage("   "))
    }

    @Test
    fun `language label is shown only for recognised languages`() {
        assertEquals("python", CodeHighlighter.languageLabel("python"))
        assertEquals("HTML", CodeHighlighter.languageLabel("HTML"))
        assertNull(CodeHighlighter.languageLabel("brainfuck"))
        assertNull(CodeHighlighter.languageLabel(""))
    }

    @Test
    fun `highlighting never drops or rewrites characters`() {
        val samples = mapOf(
            "python" to "def f(x):\n    # c\n    return \"a\\\"b\" + str(3.14)\n",
            "kotlin" to "fun main() = println(\"42\") // хвост\nval x = 0x1F",
            "java" to "class A { int x = 0x1F; } // c",
            "bash" to "for f in *.py; do echo \"\$f\"; done # c",
            "shell" to "if [ -d /tmp ]; then rm -rf /tmp/x; fi",
            "json" to "{\"a\": [1, 2.5e3, true, null]}",
            "javascript" to "const f = (x) => `a\${x}b`; /* c */",
            "typescript" to "type T = { a: string }; // c",
            "xml" to "<a href='x'>1</a><!-- c -->",
            "html" to "<p class=\"x\">hi</p>",
            "yaml" to "key: value # c\nlist:\n  - 1\n  - true",
            "sql" to "select * from t where x = 'a'; -- c",
            "rust" to "fn main() { let x: i32 = 5; } // c",
            "go" to "func main() { x := 0x10; _ = x }",
            "c" to "int main(void) { return 0; } /* c */",
            "cpp" to "#include <string>\nstd::string s = \"x\";",
            "unknown" to "same text\nkept as is",
        )
        samples.forEach { (language, code) ->
            assertEquals(language, code, CodeHighlighter.highlight(code, language).joinToString("") { it.text })
        }
    }
}
