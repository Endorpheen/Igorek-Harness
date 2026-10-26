package ru.igorek.remote.ui.markdown

import ru.igorek.remote.ui.PepeEmojis
import ru.igorek.remote.ui.PEPE_TAG
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextLayoutResult

import androidx.compose.foundation.text.ClickableText
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

@Composable
fun MarkdownText(
    markdown: String,
    modifier: Modifier = Modifier,
    maxLines: Int = Int.MAX_VALUE,
) {
    PepeEmojis.load(LocalContext.current)
    val codeBackground = MaterialTheme.colorScheme.surfaceVariant
    val codeColor = MaterialTheme.colorScheme.onSurfaceVariant
    val accent = MaterialTheme.colorScheme.primary

    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        MarkdownParser.parse(markdown).forEach { block ->
            when (block) {
                is MdBlock.Code -> CodeBlockView(block)

                is MdBlock.Heading -> LinkText(
                    text = annotate(block.spans, codeBackground, codeColor, accent),
                    style = when (block.level) {
                        1 -> MaterialTheme.typography.titleLarge
                        2 -> MaterialTheme.typography.titleMedium
                        else -> MaterialTheme.typography.titleSmall
                    },
                    maxLines = maxLines,
                )

                is MdBlock.Bullet -> {
                    val body = annotate(block.spans, codeBackground, codeColor, accent)
                    LinkText(
                        text = prefix("•  ", accent, body),
                        style = MaterialTheme.typography.bodyMedium,
                        maxLines = maxLines,
                    )
                }

                is MdBlock.Numbered -> {
                    val body = annotate(block.spans, codeBackground, codeColor, accent)
                    LinkText(
                        text = prefix("${block.marker} ", accent, body),
                        style = MaterialTheme.typography.bodyMedium,
                        maxLines = maxLines,
                    )
                }

                is MdBlock.Note -> NotePlate(block, maxLines)

                is MdBlock.Table -> MarkdownTable(block, codeBackground, codeColor, accent, maxLines)

                is MdBlock.Paragraph -> LinkText(
                    text = annotate(block.spans, codeBackground, codeColor, accent),
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = maxLines,
                )
            }
        }
    }
}

@Composable
private fun NotePlate(block: MdBlock.Note, maxLines: Int) {
    val tint = MaterialTheme.colorScheme.primary
    val codeBackground = MaterialTheme.colorScheme.surfaceVariant
    val codeColor = MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        Modifier
            .fillMaxWidth()
            .background(tint.copy(alpha = 0.12f), RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        LinkText(
            text = buildAnnotatedString {
                if (block.title.isNotEmpty()) {
                    withStyle(SpanStyle(color = tint, fontWeight = FontWeight.Bold)) { append(block.title) }
                    append(": ")
                }
                append(annotate(MarkdownParser.parseInline(block.text), codeBackground, codeColor, tint))
            },
            style = MaterialTheme.typography.bodySmall,
            maxLines = maxLines,
        )
    }
}

@Composable
private fun MarkdownTable(
    table: MdBlock.Table,
    codeBackground: Color,
    codeColor: Color,
    accent: Color,
    maxLines: Int,
) {
    val separator = MaterialTheme.colorScheme.outlineVariant
    val textMeasurer = rememberTextMeasurer()
    val density = LocalDensity.current
    val bodyStyle = MaterialTheme.typography.bodySmall
    val headerStyle = bodyStyle.copy(fontWeight = FontWeight.Bold)
    val allRows = listOf(table.header) + table.rows
    val columnCount = allRows.maxOfOrNull { it.size } ?: 0
    val columnWidths = (0 until columnCount).map { column ->
        val contentWidth = allRows.mapIndexed { rowIndex, row ->
            val spans = row.getOrNull(column).orEmpty()
            val style = if (rowIndex == 0) headerStyle else bodyStyle
            textMeasurer.measure(
                text = annotate(spans, codeBackground, codeColor, accent),
                style = style,
                softWrap = false,
                maxLines = 1,
            ).size.width
        }.maxOrNull() ?: 0
        with(density) { contentWidth.toDp() }.plus(17.dp).coerceIn(48.dp, 320.dp)
    }
    Column(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
        MarkdownTableRow(table.header, columnWidths, true, separator, codeBackground, codeColor, accent, maxLines)
        table.rows.forEach { row ->
            MarkdownTableRow(row, columnWidths, false, separator, codeBackground, codeColor, accent, maxLines)
        }
    }
}

@Composable
private fun MarkdownTableRow(
    cells: List<List<MdSpan>>,
    columnWidths: List<Dp>,
    header: Boolean,
    separator: Color,
    codeBackground: Color,
    codeColor: Color,
    accent: Color,
    maxLines: Int,
) {
    Row(Modifier.background(separator).height(IntrinsicSize.Min)) {
        columnWidths.forEachIndexed { index, width ->
            val spans = cells.getOrNull(index).orEmpty()
            Box(
                Modifier
                    .width(width)
                    .fillMaxHeight()
                    .padding(end = 1.dp, bottom = 1.dp)
                    .background(if (header) codeBackground else MaterialTheme.colorScheme.surface)
                    .padding(horizontal = 8.dp, vertical = 6.dp),
            ) {
                LinkText(
                    text = annotate(spans, codeBackground, codeColor, accent),
                    style = MaterialTheme.typography.bodySmall.copy(
                        fontWeight = if (header) FontWeight.Bold else FontWeight.Normal,
                    ),
                    color = if (header) codeColor else Color.Unspecified,
                    maxLines = maxLines,
                )
            }
        }
    }
}

internal fun prefix(marker: String, accent: Color, body: AnnotatedString): AnnotatedString =
    buildAnnotatedString {
        withStyle(SpanStyle(color = accent)) { append(marker) }
        append(body)
    }

internal fun annotate(
    spans: List<MdSpan>,
    codeBackground: Color,
    codeColor: Color,
    accent: Color,
): AnnotatedString {
    val codeStyle = SpanStyle(
        fontFamily = FontFamily.Monospace,
        background = codeBackground,
        color = codeColor,
    )
    val linkStyle = SpanStyle(color = accent, textDecoration = TextDecoration.Underline)
    return buildAnnotatedString {
        fun link(label: String, raw: String) {
            val url = MarkdownLinks.webUrl(raw)
            if (url == null) append(label) else {
                pushStringAnnotation(WEB_LINK_TAG, url)
                withStyle(linkStyle) { append(label) }
                pop()
            }
        }
        fun webText(text: String) {
            var offset = 0
            MarkdownLinks.ranges(text).forEach { range ->
                PepeEmojis.append(this, text.substring(offset, range.first))
                val label = text.substring(range.first, range.last + 1)
                link(label, label)
                offset = range.last + 1
            }
            PepeEmojis.append(this, text.substring(offset))
        }
        spans.forEach { span ->
            when (span) {
                is MdSpan.Plain -> webText(span.text)
                is MdSpan.Bold -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { webText(span.text) }
                is MdSpan.Italic -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { webText(span.text) }
                is MdSpan.CodeSpan -> withStyle(codeStyle) { append(span.text) }
                is MdSpan.Link -> link(span.text, span.url)
            }
        }
    }
}


@Composable
private fun LinkText(
    text: AnnotatedString,
    style: TextStyle,
    maxLines: Int,
    color: Color = Color.Unspecified,
) {
    if (text.getStringAnnotations(PEPE_TAG, 0, text.length).isNotEmpty()) {
        val context = LocalContext.current
        val layout = remember(text) { mutableStateOf<TextLayoutResult?>(null) }
        val click = Modifier.pointerInput(text) {
            detectTapGestures { position ->
                layout.value?.let { value ->
                    val offset = value.getOffsetForPosition(position).coerceIn(0, text.lastIndex)
                    text.getStringAnnotations(WEB_LINK_TAG, offset, offset).firstOrNull()?.let { openWebLink(context, it.item) }
                }
            }
        }
        Text(text = text, style = style, maxLines = maxLines, color = color,
            inlineContent = PepeEmojis.inlineContent(), modifier = click, onTextLayout = { layout.value = it })
        return
    }
    if (text.getStringAnnotations(WEB_LINK_TAG, 0, text.length).isEmpty()) {
        Text(text = text, style = style, maxLines = maxLines, color = color)
        return
    }
    val context = LocalContext.current
    val textColor = if (color != Color.Unspecified) color else if (style.color != Color.Unspecified) style.color else LocalContentColor.current
    ClickableText(
        text = text,
        style = style.copy(color = textColor),
        maxLines = maxLines,
        onClick = { offset ->
            text.getStringAnnotations(WEB_LINK_TAG, offset.coerceIn(0, text.lastIndex), offset.coerceIn(0, text.lastIndex)).firstOrNull()?.let { link ->
                openWebLink(context, link.item)
            }
        },
    )
}

@Composable
internal fun PlainEmojiText(value: String) {
    PepeEmojis.load(LocalContext.current)
    val text = buildAnnotatedString { PepeEmojis.append(this, value) }
    LinkText(text = text, style = MaterialTheme.typography.bodyMedium, maxLines = Int.MAX_VALUE)
}
