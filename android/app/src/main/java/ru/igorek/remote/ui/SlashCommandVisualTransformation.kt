package ru.igorek.remote.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation

private val commandPrefix = Regex("^[ \\t]*(?:/(?:help|status)(?=\\s*$)|/goal(?=\\s|$)(?:[ \\t]+(?:pause|resume|clear|edit)(?=\\s|$))?)")

class SlashCommandVisualTransformation(private val color: Color) : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val match = commandPrefix.find(text.text) ?: return TransformedText(text, OffsetMapping.Identity)
        val start = text.text.indexOf('/', match.range.first)
        val end = match.range.last + 1
        val styled = buildAnnotatedString {
            append(text)
            addStyle(SpanStyle(color = color, fontWeight = FontWeight.SemiBold), start, end)
        }
        return TransformedText(styled, OffsetMapping.Identity)
    }
}
