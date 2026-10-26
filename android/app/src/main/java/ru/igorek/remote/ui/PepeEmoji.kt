package ru.igorek.remote.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Movie
import android.os.SystemClock
import android.view.View
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

internal const val PEPE_TAG = "igorek-pepe"
internal data class PepeEntry(val id: String, val label: String, val russian: String, val file: String)
internal object PepeEmojis {
    private val valid = Regex("pepe_[a-z_]{1,32}")
    private val token = Regex(":(pepe_[a-z_]{1,32}):")
    private var entries: Map<String, PepeEntry> = emptyMap()

    @Synchronized fun load(context: Context): Map<String, PepeEntry> {
        if (entries.isNotEmpty()) return entries
        entries = runCatching {
            val raw = context.assets.open("emoji/catalog.json").bufferedReader().use { it.readText() }
            val root = Json.parseToJsonElement(raw).jsonObject
            require(root["schema_version"]?.jsonPrimitive?.intOrNull == 1)
            root.getValue("emojis").jsonArray.map { item ->
                val row = item.jsonObject
                val id = row.getValue("id").jsonPrimitive.content
                val path = row.getValue("file").jsonPrimitive.content
                require(valid.matches(id) && path == "pepe/$id.gif")
                PepeEntry(id, row.getValue("label").jsonPrimitive.content, row["label_ru"]?.jsonPrimitive?.content ?: id, path)
            }.also { require(it.size in 1..64 && it.map { row -> row.id }.distinct().size == it.size) }.associateBy { it.id }
        }.getOrDefault(emptyMap())
        return entries
    }
    fun label(entry: PepeEntry): String = ru.igorek.remote.data.AppLocale.text(entry.russian, entry.label)
    fun append(builder: AnnotatedString.Builder, text: String) {
        var start = 0
        for (found in token.findAll(text)) {
            val id = found.groupValues[1]
            if (id !in entries) continue
            builder.append(text.substring(start, found.range.first))
            builder.pushStringAnnotation(PEPE_TAG, id)
            builder.appendInlineContent(id, found.value)
            builder.pop()
            start = found.range.last + 1
        }
        builder.append(text.substring(start))
    }
    fun inlineContent(): Map<String, InlineTextContent> = entries.mapValues { (id, _) ->
        InlineTextContent(Placeholder(1.7.em, 1.7.em, PlaceholderVerticalAlign.TextCenter)) {
            AnimatedPepe(id, Modifier.fillMaxSize())
        }
    }
    fun receipt(name: String, result: String): String? = runCatching {
        require(Regex("(?:^|.*__|.*\\.)render_emoji$").matches(name))
        val value = Json.parseToJsonElement(result).jsonObject
        require(value["ok"]?.jsonPrimitive?.booleanOrNull == true && value["kind"]?.jsonPrimitive?.content == "igorek.emoji" && value["schema_version"]?.jsonPrimitive?.intOrNull == 1)
        value.getValue("emoji_id").jsonPrimitive.content.also { require(it in entries) }
    }.getOrNull()
    fun isTool(name: String): Boolean = Regex("(?:^|.*__|.*\\.)render_emoji$").matches(name)
}

private class PepeMovieView(context: Context, entry: PepeEntry) : View(context) {
    private val movie = runCatching { context.assets.open("emoji/${entry.file}").use { Movie.decodeStream(it) } }.getOrNull()
    private val start = SystemClock.uptimeMillis()
    init { contentDescription = PepeEmojis.label(entry); setLayerType(LAYER_TYPE_SOFTWARE, null) }
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val animation = movie ?: return
        if (width == 0 || height == 0 || animation.width() == 0 || animation.height() == 0) return
        animation.setTime(((SystemClock.uptimeMillis() - start) % animation.duration().coerceAtLeast(100)).toInt())
        val scale = minOf(width.toFloat()/animation.width(), height.toFloat()/animation.height())
        canvas.save()
        canvas.translate((width-animation.width()*scale)/2, (height-animation.height()*scale)/2)
        canvas.scale(scale, scale); animation.draw(canvas, 0f, 0f); canvas.restore()
        if (isAttachedToWindow && windowVisibility == VISIBLE && isShown) postInvalidateDelayed(40)
    }
    override fun onVisibilityAggregated(isVisible: Boolean) { super.onVisibilityAggregated(isVisible); if (isVisible) invalidate() }
}

@Composable
internal fun AnimatedPepe(id: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val row = remember(context, id) { PepeEmojis.load(context)[id] }
    if (row == null) Text(":$id:", modifier = modifier)
    else androidx.compose.runtime.key(id) { AndroidView(factory = { PepeMovieView(it, row) }, modifier = modifier) }
}

@Composable
internal fun PepeEmojiPicker(onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val context = LocalContext.current
    val rows = remember(context) { PepeEmojis.load(context).values.toList() }
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surface, tonalElevation = 6.dp) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(uiText("Эмодзи Пепе", "Pepe emoji"), style = MaterialTheme.typography.titleMedium)
                LazyVerticalGrid(columns = GridCells.Fixed(4), modifier = Modifier.heightIn(max=300.dp), contentPadding = PaddingValues(2.dp)) {
                    items(rows, key = { it.id }) { row ->
                        IconButton(onClick = { onPick(":"+row.id+":") }, modifier = Modifier.size(58.dp)) {
                            AnimatedPepe(row.id, Modifier.size(48.dp))
                        }
                    }
                }
            }
        }
    }
}
