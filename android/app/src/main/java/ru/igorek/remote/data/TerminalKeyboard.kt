package ru.igorek.remote.data

internal class TerminalKeyboard(private val send: (String) -> Boolean) {
    private var composing = ""

    fun compose(text: String): Boolean {
        val accepted = send("\u007f".repeat(composing.codePointCount(0, composing.length)) + text)
        if (accepted) composing = text
        return accepted
    }

    fun commit(text: String): Boolean {
        val accepted = compose(text)
        if (accepted) composing = ""
        return accepted
    }

    fun finish() { composing = "" }

    fun key(text: String): Boolean {
        finish()
        return send(text)
    }

    fun delete(before: Int, after: Int): Boolean {
        finish()
        return send("\u007f".repeat(before.coerceIn(0, 1024)) + "\u001b[3~".repeat(after.coerceIn(0, 1024)))
    }
}
