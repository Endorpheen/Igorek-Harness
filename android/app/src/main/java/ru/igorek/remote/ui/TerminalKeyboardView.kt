package ru.igorek.remote.ui

import android.content.Context
import android.text.InputType
import android.view.KeyEvent
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.widget.EditText
import ru.igorek.remote.data.TerminalKeyboard

internal class TerminalKeyboardView(context: Context, send: (String) -> Boolean) : EditText(context) {
    val keyboard = TerminalKeyboard(send)

    init {
        hint = "Ввод сразу в терминал"
        setSingleLine(true)
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        imeOptions = EditorInfo.IME_ACTION_SEND or EditorInfo.IME_FLAG_NO_EXTRACT_UI
    }

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection {
        super.onCreateInputConnection(outAttrs)
        return object : BaseInputConnection(this, false) {
            override fun commitText(text: CharSequence?, newCursorPosition: Int) = keyboard.commit(text?.toString().orEmpty())
            override fun setComposingText(text: CharSequence?, newCursorPosition: Int) = keyboard.compose(text?.toString().orEmpty())
            override fun finishComposingText(): Boolean { keyboard.finish(); return true }
            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int) = keyboard.delete(beforeLength, afterLength)
            override fun deleteSurroundingTextInCodePoints(beforeLength: Int, afterLength: Int) = keyboard.delete(beforeLength, afterLength)
            override fun performEditorAction(actionCode: Int) = keyboard.key("\r")
            override fun sendKeyEvent(event: KeyEvent): Boolean = handleKey(event)
        }
    }

    private fun handleKey(event: KeyEvent): Boolean {
        if (event.action != KeyEvent.ACTION_DOWN) return true
        val value = when (event.keyCode) {
            KeyEvent.KEYCODE_ENTER -> "\r"
            KeyEvent.KEYCODE_DEL -> "\u007f"
            KeyEvent.KEYCODE_FORWARD_DEL -> "\u001b[3~"
            KeyEvent.KEYCODE_TAB -> "\t"
            KeyEvent.KEYCODE_ESCAPE -> "\u001b"
            KeyEvent.KEYCODE_DPAD_UP -> "\u001b[A"
            KeyEvent.KEYCODE_DPAD_DOWN -> "\u001b[B"
            KeyEvent.KEYCODE_DPAD_RIGHT -> "\u001b[C"
            KeyEvent.KEYCODE_DPAD_LEFT -> "\u001b[D"
            else -> {
                val code = event.unicodeChar
                if (code == 0) return false
                if (event.isCtrlPressed && code in 64..127) (code and 31).toChar().toString()
                else String(Character.toChars(code))
            }
        }
        return keyboard.key(value)
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean =
        if (keyCode == KeyEvent.KEYCODE_BACK) super.onKeyDown(keyCode, event) else handleKey(event)
}
