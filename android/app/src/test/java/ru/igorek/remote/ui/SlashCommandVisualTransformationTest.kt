package ru.igorek.remote.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontWeight
import org.junit.Assert.*
import org.junit.Test

class SlashCommandVisualTransformationTest {
    private val accent = Color(0xFF4567AB)
    private val transformation = SlashCommandVisualTransformation(accent)

    @Test fun `goal command is colored but objective and leading whitespace remain untouched`() {
        for ((input, start, end) in listOf(
            Triple("/goal Build feature", 0, 5),
            Triple("  /goal \uD83D\uDE80 итог", 2, 7),
            Triple("/goal\nMultiline objective", 0, 5),
            Triple("/goal", 0, 5),
        )) {
            val result = transformation.filter(AnnotatedString(input))
            assertEquals(input, result.text.text)
            val span = result.text.spanStyles.single()
            assertEquals(start, span.start)
            assertEquals(end, span.end)
            assertEquals(accent, span.item.color)
            assertEquals(FontWeight.SemiBold, span.item.fontWeight)
            for (offset in 0..input.length) {
                assertEquals(offset, result.offsetMapping.originalToTransformed(offset))
                assertEquals(offset, result.offsetMapping.transformedToOriginal(offset))
            }
        }
    }

    @Test fun `only recognized complete actions are included in the command span`() {
        for (command in listOf("/help", "/status")) {
            val input = "$command "
            val result = transformation.filter(AnnotatedString(input))
            assertEquals(command.length, result.text.spanStyles.single().end)
            assertEquals(input, result.text.text)
        }
        for (action in listOf("pause", "resume", "clear", "edit")) {
            val command = "/goal $action"
            for (input in listOf(command, "$command ", "$command objective")) {
                val result = transformation.filter(AnnotatedString(input))
                assertEquals(command.length, result.text.spanStyles.single().end)
                assertEquals(input, result.text.text)
            }
        }
        for (input in listOf("/goal paused", "/goal pauseful", "/goal editX", "/goal arbitrary")) {
            assertEquals(5, transformation.filter(AnnotatedString(input)).text.spanStyles.single().end)
        }
        assertEquals(10, transformation.filter(AnnotatedString("/goal\tedit revised")).text.spanStyles.single().end)
    }

    @Test fun `ordinary text unknown commands and partial or differently cased prefixes have no style`() {
        for (input in listOf("", "hello", "hello /goal", "/go", "/goalkeeper", "/GOAL x", "/tmp/file", "\n/goal", "/plan", "/planish", "/statusbar", "/helpful", "/fast", "/plan a task")) {
            val original = AnnotatedString(input)
            val result = transformation.filter(original)
            assertEquals(original, result.text)
            assertTrue(input, result.text.spanStyles.isEmpty())
        }
    }

    @Test fun `existing annotated styles survive and accents can follow the current theme`() {
        val original = buildAnnotatedString { withStyle(SpanStyle(color = Color.Red)) { append("/goal example") } }
        val result = SlashCommandVisualTransformation(Color.Green).filter(original)
        assertEquals(original.text, result.text.text)
        assertEquals(Color.Red, result.text.spanStyles.first().item.color)
        assertEquals(Color.Green, result.text.spanStyles.last().item.color)
    }
}
