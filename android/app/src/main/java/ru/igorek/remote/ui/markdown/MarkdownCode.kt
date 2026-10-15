package ru.igorek.remote.ui.markdown

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import ru.igorek.remote.ui.CopyButton
import ru.igorek.remote.ui.Info
import ru.igorek.remote.ui.Success
import ru.igorek.remote.ui.Warning

/** Copy text: original block content as-is, without highlighting tokens or truncation. */
internal fun codeCopyText(block: MdBlock.Code): String = block.text

@Composable
internal fun CodeBlockView(block: MdBlock.Code) {
    val label = CodeHighlighter.languageLabel(block.language)
    val highlighted = remember(block.text, block.language) {
        CodeHighlighter.highlight(block.text, block.language)
    }
    val colors = CodeTokenKind.entries.associateWith { codeTokenColor(it) }
    val annotated = remember(highlighted, colors) {
        buildAnnotatedString {
            highlighted.forEach { token ->
                withStyle(SpanStyle(color = colors.getValue(token.kind))) { append(token.text) }
            }
        }
    }
    val shape = RoundedCornerShape(10.dp)
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface, shape)
            .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.45f), shape),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(
                    if (label != null) {
                        MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.7f)
                    } else {
                        Color.Transparent
                    },
                )
                .padding(start = 10.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (label != null) {
                Text(
                    label,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                Spacer(Modifier.weight(1f))
            }
            CopyButton(codeCopyText(block))
        }
        Box(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(10.dp),
        ) {
            Text(
                text = annotated,
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

@Composable
private fun codeTokenColor(kind: CodeTokenKind): Color = when (kind) {
    CodeTokenKind.Plain -> MaterialTheme.colorScheme.onSurface
    CodeTokenKind.Keyword -> MaterialTheme.colorScheme.primary
    CodeTokenKind.String -> Success
    CodeTokenKind.Comment -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.75f)
    CodeTokenKind.Number -> Warning
    CodeTokenKind.Function -> Info
    CodeTokenKind.Type -> MaterialTheme.colorScheme.secondary
}
