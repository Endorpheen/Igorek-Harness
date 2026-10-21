package ru.igorek.remote.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.History
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.launch
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ChangedFileDto
import ru.igorek.remote.data.ChangedFilesResponse
import ru.igorek.remote.data.CheckpointDto

internal const val CHECKPOINTS_FLING_VELOCITY = 900f
internal const val CHECKPOINTS_OPEN_FRACTION = 0.35f

private const val CHECKPOINTS_PANEL_ANIM_MS = 220
private const val CHECKPOINTS_PANEL_WIDTH_FRACTION = 0.92f
private val CHECKPOINTS_PANEL_MAX_WIDTH = 420.dp

private val CHECKPOINT_CLOCK_FORMAT = DateTimeFormatter.ofPattern("HH:mm")
private val CHECKPOINT_DATE_FORMAT = DateTimeFormatter.ofPattern("dd.MM.yyyy")

internal fun stripCheckpointPrefix(reason: String): String {
    val trimmed = reason.trim()
    if (!trimmed.startsWith("checkpoint:", ignoreCase = true)) return trimmed
    return trimmed.substring("checkpoint:".length).trim()
}

internal fun checkpointShortLabel(hash: String, shortHash: String): String =
    shortHash.trim().ifBlank { hash.trim().take(7) }

internal fun checkpointInstant(timestamp: String, zone: ZoneId = ZoneId.systemDefault()): Instant? {
    val raw = timestamp.trim()
    if (raw.isEmpty()) return null
    return runCatching { Instant.parse(raw) }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(raw).toInstant() }.getOrNull()
        ?: runCatching { LocalDateTime.parse(raw).atZone(zone).toInstant() }.getOrNull()
}

internal fun checkpointTimeLabel(
    timestamp: String,
    language: AppLanguage = AppLocale.current,
    nowMillis: Long = System.currentTimeMillis(),
    zone: ZoneId = ZoneId.systemDefault(),
): String {
    val instant = checkpointInstant(timestamp, zone) ?: return timestamp.trim()
    val moment = instant.atZone(zone)
    val today = Instant.ofEpochMilli(nowMillis).atZone(zone).toLocalDate()
    val clock = CHECKPOINT_CLOCK_FORMAT.format(moment)
    return when (moment.toLocalDate()) {
        today -> if (language == AppLanguage.Russian) "сегодня, $clock" else "today, $clock"
        today.minusDays(1) -> if (language == AppLanguage.Russian) "вчера, $clock" else "yesterday, $clock"
        else -> "${CHECKPOINT_DATE_FORMAT.format(moment)} $clock"
    }
}

internal fun checkpointsEmptyState(
    available: Boolean,
    language: AppLanguage = AppLocale.current,
): String? {
    if (available) return null
    return if (language == AppLanguage.Russian) "История файлов недоступна" else "File history unavailable"
}

internal fun checkpointCountLabel(count: Int, language: AppLanguage = AppLocale.current): String {
    if (language == AppLanguage.English) return "$count ${if (count == 1) "version" else "versions"}"
    val modulo100 = count % 100
    val modulo10 = count % 10
    val noun = when {
        modulo100 in 11..14 -> "версий"
        modulo10 == 1 -> "версия"
        modulo10 in 2..4 -> "версии"
        else -> "версий"
    }
    return "$count $noun"
}

internal fun checkpointsFooterLabel(
    shown: Int,
    hasMore: Boolean,
    language: AppLanguage = AppLocale.current,
): String? {
    if (!hasMore) return null
    val count = checkpointCountLabel(shown, language)
    return if (language == AppLanguage.Russian) "Показаны последние $count" else "Showing the last $count"
}

internal fun checkpointChangesTitle(
    checkpoint: CheckpointDto,
    language: AppLanguage = AppLocale.current,
): String {
    val short = checkpointShortLabel(checkpoint.hash, checkpoint.shortHash)
    if (short.isEmpty()) {
        return if (language == AppLanguage.Russian) "Файлы версии" else "Files in version"
    }
    return if (language == AppLanguage.Russian) "Файлы версии $short" else "Files in version $short"
}

internal fun checkpointDiffSubtitle(
    from: String,
    shortHash: String,
    language: AppLanguage = AppLocale.current,
): String? {
    val version = from.trim().ifBlank { shortHash.trim() }
    if (version.isEmpty()) return null
    val label = if (version.length > 12) version.take(8) else version
    return if (language == AppLanguage.Russian) "версия $label ↔ сейчас" else "version $label ↔ now"
}

internal fun checkpointsDrawerTarget(offsetPx: Float, widthPx: Float, velocity: Float): Boolean {
    if (widthPx <= 0f) return true
    if (velocity <= -CHECKPOINTS_FLING_VELOCITY) return true
    if (velocity >= CHECKPOINTS_FLING_VELOCITY) return false
    return offsetPx < widthPx * CHECKPOINTS_OPEN_FRACTION
}

/**
 * Version drawer host. Custom implementation instead of ModalNavigationDrawer:
 * RTL trick for drawer flips the entire screen layout, and its opening gesture is tied to the edge.
 * Here, a horizontally draggable component exists on the message feed and panel, while vertical scrolling in LazyColumn and input fields is unaffected—gestures are distributed along touch slop axes.
 */
@Composable
internal fun CheckpointsDrawerHost(
    open: Boolean,
    onOpenChange: (Boolean) -> Unit,
    panelContent: @Composable (Modifier) -> Unit,
    content: @Composable (Modifier) -> Unit,
) {
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val panelWidth = minOf(CHECKPOINTS_PANEL_MAX_WIDTH, maxWidth * CHECKPOINTS_PANEL_WIDTH_FRACTION)
        val panelWidthPx = with(LocalDensity.current) { panelWidth.toPx() }
        val scope = rememberCoroutineScope()
        // Single owner of the animated offset: never re-key this on open/panelWidthPx,
        // so a swipe still works after the panel was opened and closed before.
        val offset = remember { Animatable(if (open) 0f else panelWidthPx) }
        var settleToken by remember { mutableIntStateOf(0) }

        LaunchedEffect(open, panelWidthPx, settleToken) {
            offset.animateTo(
                targetValue = if (open) 0f else panelWidthPx,
                animationSpec = tween(CHECKPOINTS_PANEL_ANIM_MS),
            )
        }

        val dragState = rememberDraggableState { delta ->
            scope.launch { offset.snapTo((offset.value + delta).coerceIn(0f, panelWidthPx)) }
        }
        // settle must stay non-suspending: an inline animateTo here would be preempted by
        // the LaunchedEffect above (MutatorMutex latest-wins) and kill the gesture coroutine.
        // Always bump settleToken, even when the target did not change, so the offset animates.
        val settle: (Float) -> Unit = { velocity ->
            val shouldOpen = checkpointsDrawerTarget(offset.value, panelWidthPx, velocity)
            onOpenChange(shouldOpen)
            settleToken++
        }
        val dragModifier = Modifier.draggable(
            state = dragState,
            orientation = Orientation.Horizontal,
            onDragStopped = { velocity -> settle(velocity) },
        )

        content(dragModifier)

        // Read `open` outside the derived state; only offset.value is tracked inside.
        val nearOpen by remember(panelWidthPx) { derivedStateOf { offset.value < panelWidthPx - 0.5f } }
        if (open || nearOpen) {
            Box(
                Modifier
                    .matchParentSize()
                    .graphicsLayer { alpha = 0.55f * (1f - offset.value / panelWidthPx).coerceIn(0f, 1f) }
                    .background(Color.Black)
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                    ) { onOpenChange(false) }
                    .draggable(
                        state = dragState,
                        orientation = Orientation.Horizontal,
                        onDragStopped = { velocity -> settle(velocity) },
                    ),
            )
            Box(
                Modifier
                    .align(Alignment.CenterEnd)
                    .width(panelWidth)
                    .fillMaxHeight()
                    .graphicsLayer { translationX = offset.value },
            ) {
                panelContent(dragModifier.fillMaxSize())
            }
        }
    }
}

@Composable
internal fun CheckpointsPanel(
    sessionId: String,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val language = LocalAppLanguage.current
    val checkpoints by Bridge.sessionCheckpoints.collectAsState()
    val loading by Bridge.checkpointsLoading.collectAsState()
    val error by Bridge.checkpointsError.collectAsState()
    val changes by Bridge.checkpointChanges.collectAsState()
    val changesLoading by Bridge.checkpointChangesLoading.collectAsState()
    val changesError by Bridge.checkpointChangesError.collectAsState()
    val diff by Bridge.checkpointDiff.collectAsState()
    var selected by remember { mutableStateOf<CheckpointDto?>(null) }
    var diffPath by remember { mutableStateOf<String?>(null) }
    val state = checkpoints

    Surface(
        modifier = modifier,
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 4.dp,
    ) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Default.History,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(18.dp),
                )
                Column(Modifier.weight(1f)) {
                    Text(
                        uiText("Версии файлов", "File versions"),
                        style = MaterialTheme.typography.titleMedium,
                    )
                    state?.workdir?.takeIf { it.isNotBlank() }?.let { workdir ->
                        Text(
                            workdir,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontFamily = FontFamily.Monospace,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = uiText("Закрыть", "Close"))
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
            Box(Modifier.fillMaxWidth().weight(1f)) {
                when {
                    error != null && state == null -> CheckpointsErrorState(
                        message = error.orEmpty(),
                        onRetry = { Bridge.refreshSessionCheckpoints(sessionId) },
                    )
                    state == null -> Row(
                        Modifier.fillMaxWidth().padding(20.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        if (loading) {
                            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                        }
                        Text(
                            uiText("Загружаем версии…", "Loading versions…"),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    !state.available -> CheckpointsMessage(
                        checkpointsEmptyState(false, language).orEmpty(),
                    )
                    state.checkpoints.isEmpty() -> CheckpointsMessage(
                        uiText("Изменений пока нет", "No changes yet"),
                    )
                    else -> CheckpointsList(
                        checkpoints = state.checkpoints,
                        hasMore = state.hasMore,
                        language = language,
                        onCheckpointClick = { checkpoint ->
                            selected = checkpoint
                            Bridge.openCheckpointChanges(checkpoint.hash)
                        },
                    )
                }
            }
        }
    }

    selected?.let { checkpoint ->
        CheckpointChangesDialog(
            checkpoint = checkpoint,
            changes = changes,
            loading = changesLoading,
            error = changesError,
            language = language,
            onFileClick = { file ->
                diffPath = file.path
                Bridge.openCheckpointDiff(checkpoint.hash, file.path)
            },
            onDismiss = {
                selected = null
                diffPath = null
                Bridge.clearCheckpointChanges()
                Bridge.clearCheckpointDiff()
            },
            onRetry = { Bridge.openCheckpointChanges(checkpoint.hash) },
        )
    }

    diffPath?.let { path ->
        val checkpoint = selected
        DiffDialog(
            path = path,
            diff = diff,
            subtitle = checkpointDiffSubtitle(
                from = diff?.from.orEmpty(),
                shortHash = checkpointShortLabel(
                    checkpoint?.hash.orEmpty(),
                    checkpoint?.shortHash.orEmpty(),
                ),
                language = language,
            ),
            onDismiss = {
                diffPath = null
                Bridge.clearCheckpointDiff()
            },
        )
    }
}

@Composable
private fun CheckpointsList(
    checkpoints: List<CheckpointDto>,
    hasMore: Boolean,
    language: AppLanguage,
    onCheckpointClick: (CheckpointDto) -> Unit,
) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        items(checkpoints, key = { it.hash.ifBlank { "${it.timestamp}-${it.reason}" } }) { checkpoint ->
            CheckpointRow(checkpoint, language, onCheckpointClick)
        }
        checkpointsFooterLabel(checkpoints.size, hasMore, language)?.let { footer ->
            item(key = "checkpoints-footer") {
                Text(
                    footer,
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun CheckpointRow(
    checkpoint: CheckpointDto,
    language: AppLanguage,
    onClick: (CheckpointDto) -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable { onClick(checkpoint) }
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                checkpointTimeLabel(checkpoint.timestamp, language),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                stripCheckpointPrefix(checkpoint.reason).ifBlank {
                    uiText("Без описания", "No description")
                },
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Text(
            checkpointShortLabel(checkpoint.hash, checkpoint.shortHash),
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.primary,
        )
    }
}

@Composable
private fun CheckpointsMessage(message: String) {
    Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.TopCenter) {
        Text(
            message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun CheckpointsErrorState(message: String, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        ErrorNotificationEffect(message, "checkpoints")
        TextButton(onClick = onRetry) { Text(uiText("Повторить", "Retry")) }
    }
}

@Composable
private fun CheckpointChangesDialog(
    checkpoint: CheckpointDto,
    changes: ChangedFilesResponse?,
    loading: Boolean,
    error: String?,
    language: AppLanguage,
    onFileClick: (ChangedFileDto) -> Unit,
    onDismiss: () -> Unit,
    onRetry: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(checkpointChangesTitle(checkpoint, language)) },
        text = {
            when {
                error != null && changes == null -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    ErrorNotificationEffect(error, "checkpoints")
                    TextButton(onClick = onRetry) { Text(uiText("Повторить", "Retry")) }
                }
                changes == null -> Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (loading) {
                        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    }
                    Text(
                        uiText("Загружаем файлы версии…", "Loading version files…"),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                else -> ChangedFilesSummaryList(changes, language, onFileClick)
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(uiText("Закрыть", "Close")) }
        },
    )
}
