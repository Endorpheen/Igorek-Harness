package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test

class TerminalKeyboardTest {
    @Test fun consecutiveCommandsHaveSeparateEnterEvents() {
        val sent = mutableListOf<String>()
        val keyboard = TerminalKeyboard { sent += it; true }
        keyboard.commit("pwd")
        keyboard.key("\r")
        keyboard.commit("uname")
        keyboard.key("\r")
        assertEquals("pwd\runame\r", sent.joinToString(""))
    }

    @Test fun completionHistoryAndBackspaceUseOnlyShellLine() {
        val sent = mutableListOf<String>()
        val keyboard = TerminalKeyboard { sent += it; true }
        keyboard.commit("cat rep")
        keyboard.key("\t")
        keyboard.key("\t")
        keyboard.delete(1, 0)
        keyboard.commit("t")
        keyboard.key("\r")
        keyboard.key("\u001b[A")
        keyboard.key("\r")
        assertEquals("cat rep\t\t\u007ft\r\u001b[A\r", sent.joinToString(""))
    }

    @Test fun imeCompositionReplacesPreviousTextWithoutDuplicatingCommit() {
        val sent = mutableListOf<String>()
        val keyboard = TerminalKeyboard { sent += it; true }
        keyboard.compose("p")
        keyboard.compose("pw")
        keyboard.commit("pwd")
        keyboard.key("\t")
        keyboard.commit("x")
        assertEquals(listOf("p", "\u007fpw", "\u007f\u007fpwd", "\t", "x"), sent)
    }

    @Test fun rejectedCompositionDoesNotEraseUnsentText() {
        var accepted = false
        val sent = mutableListOf<String>()
        val keyboard = TerminalKeyboard { sent += it; accepted }
        assertFalse(keyboard.compose("pwd"))
        accepted = true
        assertTrue(keyboard.commit("uname"))
        assertEquals(listOf("pwd", "uname"), sent)
    }
}
