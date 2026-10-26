package ru.igorek.remote.ui

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.booleanOrNull
import ru.igorek.remote.data.McpChatRow
import ru.igorek.remote.data.mergeMcpChatRows
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay

import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.snapshotFlow
import androidx.activity.compose.BackHandler
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.SecuritySettingsResponse
import ru.igorek.remote.data.NotificationEntry
import ru.igorek.remote.data.unreadNotificationCount
import ru.igorek.remote.data.TimelineItem
import ru.igorek.remote.data.securityBadgeVisible
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.max
import kotlin.math.roundToInt

private const val MAX_ATTACHMENT_BYTES = 50L * 1024 * 1024
private const val IMAGE_MAX_SIDE = 1600

private class PendingAttachment(
    val id: String,
    val name: String,
    val mimeType: String,
    val bytes: ByteArray,
)

private val IMAGE_EXTENSIONS = setOf("jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp")

private val DOCUMENT_MIME_TYPES = arrayOf(
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "*/*",
)

private fun queryDisplayName(context: Context, uri: Uri): String {
    var name = uri.lastPathSegment?.substringAfterLast('/').orEmpty()
    runCatching {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (index >= 0) cursor.getString(index)?.let { name = it }
                }
            }
    }
    return name.ifBlank { "file" }
}

private fun readUriBytes(context: Context, uri: Uri): ByteArray? {
    return context.contentResolver.openInputStream(uri)?.use { input ->
        val output = ByteArrayOutputStream()
        val chunk = ByteArray(65536)
        var total = 0L
        while (true) {
            val count = input.read(chunk)
            if (count < 0) break
            total += count
            if (total > MAX_ATTACHMENT_BYTES) return null
            output.write(chunk, 0, count)
        }
        output.toByteArray()
    }
}

private fun compressImage(raw: ByteArray): ByteArray? {
    val bitmap = BitmapFactory.decodeByteArray(raw, 0, raw.size) ?: return null
    val longest = max(bitmap.width, bitmap.height)
    val scaled = if (longest > IMAGE_MAX_SIDE) {
        val ratio = IMAGE_MAX_SIDE.toFloat() / longest
        Bitmap.createScaledBitmap(
            bitmap,
            (bitmap.width * ratio).roundToInt().coerceAtLeast(1),
            (bitmap.height * ratio).roundToInt().coerceAtLeast(1),
            true,
        )
    } else {
        bitmap
    }
    val output = ByteArrayOutputStream()
    scaled.compress(Bitmap.CompressFormat.JPEG, 80, output)
    if (scaled !== bitmap) scaled.recycle()
    bitmap.recycle()
    return output.toByteArray()
}

private fun buildPendingAttachment(context: Context, uri: Uri): PendingAttachment? {
    val raw = readUriBytes(context, uri) ?: return null
    val resolvedType = context.contentResolver.getType(uri)
    val name = queryDisplayName(context, uri)
    val extension = name.substringAfterLast('.', "").lowercase()
    val isImage = resolvedType?.startsWith("image/") == true || extension in IMAGE_EXTENSIONS
    if (isImage) {
        val compressed = compressImage(raw)
        if (compressed != null) {
            val jpegName = name.substringBeforeLast('.', name).ifBlank { "photo" } + ".jpg"
            return PendingAttachment(UUID.randomUUID().toString(), jpegName, "image/jpeg", compressed)
        }
    }
    val mime = resolvedType ?: if (isImage) "image/jpeg" else "application/octet-stream"
    return PendingAttachment(UUID.randomUUID().toString(), name, mime, raw)
}

private suspend fun uploadPending(sessionId: String, item: PendingAttachment): String =
    suspendCancellableCoroutine { continuation ->
        Bridge.uploadAttachment(sessionId, item.bytes, item.name, item.mimeType) { uploadId, error ->
            when {
                error != null -> continuation.resumeWithException(error)
                uploadId == null -> continuation.resumeWithException(
                    IOException(AppLocale.text("Сервер не вернул идентификатор вложения", "The server did not return an attachment ID"))
                )
                else -> continuation.resume(uploadId)
            }
        }
    }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionScreen(
    sessionId: String,
    initialSkillId: String? = null,
    onInitialSkillConsumed: () -> Unit = {},
    onMenu: () -> Unit,
    onBack: () -> Unit,
    onOpenSession: (String) -> Unit,
) {
    val regularTimeline by Bridge.timeline.collectAsState()
    var mcpRows by remember(sessionId) { mutableStateOf(Bridge.mcpChat(sessionId)) }
    fun updateMcpRows(rows: List<McpChatRow>) {
        if (rows == mcpRows) return
        Bridge.saveMcpChat(sessionId, rows)
        mcpRows = rows
    }
    val completeTimeline = mergeMcpChatRows(regularTimeline, mcpRows)
    val timeline = completeTimeline.filter { ru.igorek.remote.data.notificationFailureText(it) == null }
    val approvals by Bridge.approvals.collectAsState()
    val agentTree by Bridge.agentSessions.collectAsState()
    val detail by Bridge.sessionDetail.collectAsState()
    val sessions by Bridge.sessions.collectAsState()
    val connection by Bridge.connection.collectAsState()
    val sessionChanges by Bridge.sessionChanges.collectAsState()
    val sessionChangesDiff by Bridge.sessionChangesDiff.collectAsState()
    val extensions by Bridge.extensions.collectAsState()
    val securitySettings by Bridge.securitySettings.collectAsState()
    val switchingProfile by Bridge.profileSwitching.collectAsState()
    val listState = rememberLazyListState()

    var draft by rememberSaveable(sessionId) { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var stopMenu by remember { mutableStateOf(false) }
    var attachMenu by remember { mutableStateOf(false) }
    var emojiPicker by remember { mutableStateOf(false) }
    if (emojiPicker) PepeEmojiPicker(onDismiss = { emojiPicker = false }, onPick = { token ->
        draft += token
        emojiPicker = false
    })

    var mcpOpen by remember(sessionId) { mutableStateOf(false) }
    var selectedMcp by remember(sessionId) { mutableStateOf<JsonObject?>(null) }
    var mcpRunning by remember(sessionId) { mutableStateOf(false) }
    var mcpGeneration by remember(sessionId) { mutableStateOf(0) }
    var mcpJob by remember(sessionId) { mutableStateOf<Job?>(null) }
    var mcpClearJob by remember(sessionId) { mutableStateOf<Job?>(null) }
    fun disconnectMcp() {
        if (mcpRunning) updateMcpRows(mcpRows.map { row ->
            if (row.inProgress) row.copy(item = TimelineItem.Status(row.item.id, "Вызов MCP остановлен"), inProgress = false) else row
        })
        mcpGeneration++; mcpJob?.cancel(); mcpJob = null; mcpRunning = false
        if (selectedMcp != null) draft = ""
        selectedMcp = null; mcpClearJob = Bridge.clearMcpAsync()
    }
    if (mcpOpen) McpTaskDialog(onDismiss = { mcpOpen = false }, onSelect = { entry ->
        disconnectMcp(); selectedMcp = entry
    })
    val mcpLifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(sessionId, mcpLifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP && (selectedMcp != null || mcpRows.isNotEmpty())) disconnectMcp()
        }
        mcpLifecycle.addObserver(observer)
        onDispose { if (selectedMcp != null || mcpRows.isNotEmpty()) disconnectMcp(); mcpLifecycle.removeObserver(observer) }
    }
    var notificationsOpen by remember { mutableStateOf(false) }
    var diagnosticSupportOpen by remember { mutableStateOf(false) }
    val bridgeNotifications by Bridge.notifications.collectAsState()
    var changesOpen by remember { mutableStateOf(false) }
    var checkpointsOpen by remember(sessionId) { mutableStateOf(false) }
    var reviewTab by remember(sessionId) { mutableStateOf(SessionReviewTab.Versions) }
    var diffPath by remember { mutableStateOf<String?>(null) }
    var pending by remember { mutableStateOf<List<PendingAttachment>>(emptyList()) }
    var selectedSkillIds by rememberSaveable(sessionId) { mutableStateOf<List<String>>(emptyList()) }
    var attachError by remember(sessionId) { mutableStateOf<String?>(null) }
    var busyApproval by remember { mutableStateOf<String?>(null) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val attachmentReadError = uiText(
        "Не удалось прочитать вложение или оно слишком большое",
        "Could not read the attachment or it is too large",
    )
    val attachmentUploadError = uiText("Не удалось загрузить вложение", "Could not upload the attachment")
    val sendNowError = uiText("Не удалось отправить сообщение сейчас", "Could not send message now")
    LaunchedEffect(sessionId, attachError) {
        val text = attachError?.trim().orEmpty()
        if (text.isNotEmpty()) {
            Bridge.reportUiError(text, "session-input:$sessionId")
            attachError = null
        }
    }
    val notificationEntries = ru.igorek.remote.data.visibleNotifications(bridgeNotifications).sortedByDescending { it.lastSeen }
    val notificationCount = unreadNotificationCount(notificationEntries)
    fun markAllNotificationsRead() {
        Bridge.markNotificationsRead()
    }
    fun closeNotificationCenter() {
        markAllNotificationsRead()
        notificationsOpen = false
    }
    BackHandler(onBack = onBack)
    // Version panel overlays the screen: while it is open, the system’s «Back» button
    // closes it instead of the session.
    BackHandler(enabled = checkpointsOpen) { checkpointsOpen = false }

    val addUri: (Uri) -> Unit = { uri ->
        scope.launch {
            val item = withContext(Dispatchers.IO) {
                runCatching { buildPendingAttachment(context, uri) }.getOrNull()
            }
            if (item != null) {
                pending = pending + item
                attachError = null
            } else {
                attachError = attachmentReadError
            }
        }
    }
    val imagePicker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        uri?.let(addUri)
    }
    val documentPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(addUri)
    }

    LaunchedEffect(sessionId) {
        Bridge.openSession(sessionId)
        Bridge.refreshApprovals()
        Bridge.refreshSecuritySettings()
        while (true) {
            kotlinx.coroutines.delay(10_000)
            Bridge.refreshApprovals()
            Bridge.refreshAgentSessions(sessionId)
            Bridge.refreshSessions()
            Bridge.refreshFiles(sessionId)
            Bridge.refreshSessionChanges(sessionId)
            if (checkpointsOpen) Bridge.refreshSessionCheckpoints(sessionId)
            Bridge.refreshSecuritySettings()
        }
    }
    LaunchedEffect(checkpointsOpen, sessionId) {
        if (checkpointsOpen) Bridge.refreshSessionCheckpoints(sessionId)
    }
    // Automatic downward scrolling: while the user hasn’t manually pulled the history, the feed scrolls behind new
    // cards. The decision to ‘stick to the bottom’ is based on the actual list layout, not the timeline size:
    // pending confirmation cards shift indices, and a batch of three or more new cards previously
    // completely disabled scrolling—an old visible index was compared against the already expanded tail of the feed.
    var followTail by remember(sessionId) { mutableStateOf(true) }
    LaunchedEffect(listState) {
        listState.interactionSource.interactions.collect { interaction ->
            // User manually pulled the feed—stopping following its movement.
            if (interaction is DragInteraction.Start) followTail = false
        }
    }
    LaunchedEffect(listState) {
        // When scrolling (gesture, inertia, our animation) stops, we reassess based on fact:
        // Reached the bottom—stick to the tail again; remained above—do not obstruct reading.
        snapshotFlow { listState.isScrollInProgress }
            .filter { !it }
            .collect {
                val info = listState.layoutInfo
                tailFollowState(
                    lastVisibleIndex = info.visibleItemsInfo.lastOrNull()?.index ?: -1,
                    totalItems = info.totalItemsCount,
                )?.let { follow -> followTail = follow }
            }
    }

    val descendantIds = if (agentTree.rootSessionId == sessionId) agentTree.sessions.map { it.sessionId }.toSet() else emptySet()
    val sessionApprovals = approvals.filter { it.sessionId == sessionId || it.sessionId in descendantIds }
    // Cards awaiting confirmation are placed above the timeline in the list, so their count is needed
    // to determine the actual bottom of the list.
    val pendingApprovalCards = sessionApprovals.filter { approval ->
        timeline.none {
            it is TimelineItem.ApprovalNotice &&
                it.taskId == approval.taskId && it.requestId == approval.requestId
        }
    }
    val feedBottom = feedBottomIndex(pendingApprovalCards.size, timeline.size)
    suspend fun scrollLatestCard(animate: Boolean) {
        if (feedBottom < 0) return
        if (animate) listState.animateScrollToItem(feedBottom) else listState.scrollToItem(feedBottom)
        val layout = listState.layoutInfo
        val lastItem = layout.visibleItemsInfo.firstOrNull { it.index == feedBottom } ?: return
        val remaining = (lastItem.offset + lastItem.size - layout.viewportEndOffset).coerceAtLeast(0)
        if (remaining > 0) {
            if (animate) listState.animateScrollBy(remaining.toFloat())
            else listState.scrollBy(remaining.toFloat())
        }
    }
    LaunchedEffect(timeline, pendingApprovalCards.size, sessionId) {
        if (!followTail || feedBottom < 0) return@LaunchedEffect
        val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
        val instantJump = feedScrollNeedsInstantJump(lastVisible, feedBottom)
        scrollLatestCard(animate = !instantJump)
    }
    val summary = sessions.firstOrNull { it.sessionId == sessionId }
    var microphoneError by remember(sessionId) { mutableStateOf(false) }
    val microphonePermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { allowed ->
        if (allowed) ru.igorek.remote.service.VoiceCalls.open(context, sessionId)
        microphoneError = !allowed
    }
    if (microphoneError) AlertDialog(
        onDismissRequest = { microphoneError = false },
        title = { Text(uiText("Нет доступа к микрофону", "Microphone permission required")) },
        text = { Text(uiText("Разреши доступ к микрофону в настройках приложения, чтобы общаться голосом.", "Allow microphone access in app settings to use voice calls.")) },
        confirmButton = { TextButton(onClick = { microphoneError = false }) { Text("OK") } },
    )
    var composerFocused by remember { mutableStateOf(false) }
    val currentDetail = detail?.takeIf { it.sessionId == sessionId }
    val profile = (currentDetail?.meta?.get("profile") as? JsonPrimitive)?.content ?: summary?.profile.orEmpty()
    val composerModel = (currentDetail?.meta?.get("model") as? JsonPrimitive)?.content?.takeIf { it.isNotBlank() }
        ?: currentDetail?.model?.takeIf { it.isNotBlank() }
        ?: summary?.model.orEmpty()
    val isolation = summary?.isolation
        ?: detail?.takeIf { it.sessionId == sessionId }?.isolation
    val runningTaskId = if (summary != null) summary.runningTaskId
        else detail?.takeIf { it.sessionId == sessionId }?.runningTaskId
    val sessionActive = summary?.active == true || runningTaskId != null
    val streaming = timeline.any { item ->
        when (item) {
            is TimelineItem.AssistantText -> item.streaming
            is TimelineItem.Reasoning -> item.streaming
            else -> false
        }
    }
    val work = workPhase(
        activeStatus = summary?.activeStatus?.takeIf { it.isNotBlank() } ?: summary?.lastTaskStatus.orEmpty(),
        pendingApprovals = summary?.pendingApprovals ?: 0,
        streaming = streaming,
        hasRunningTask = runningTaskId != null,
    )
    val skillToken = draft.takeIf { it.startsWith("$") && it.none { char -> char.isWhitespace() } }
        ?.removePrefix("$")
    val skillMatches = if (skillToken == null || extensions.profile != profile) emptyList() else {
        extensions.skills.filter {
            it.enabled && it.supported && it.skillId !in selectedSkillIds &&
                (skillToken.isBlank() || it.name.contains(skillToken, true) ||
                    it.description.contains(skillToken, true))
        }.take(6)
    }

    LaunchedEffect(profile) {
        if (profile.isNotBlank()) Bridge.refreshExtensions(profile)
    }
    LaunchedEffect(initialSkillId) {
        val skillId = initialSkillId
        if (!skillId.isNullOrBlank() && skillId !in selectedSkillIds) {
            selectedSkillIds = selectedSkillIds + skillId
            onInitialSkillConsumed()
        }
    }

    CheckpointsDrawerHost(
        open = checkpointsOpen,
        onOpenChange = { checkpointsOpen = it },
        panelContent = { panelModifier ->
            SessionReviewPanel(
                sessionId = sessionId,
                selectedTab = reviewTab,
                onTabSelected = { tab ->
                    reviewTab = tab
                    if (tab == SessionReviewTab.Agents) Bridge.refreshAgentSessions(sessionId)
                    if (tab == SessionReviewTab.Changes) Bridge.refreshSessionChanges(sessionId)
                },
                onDismiss = { checkpointsOpen = false },
                onOpenSession = onOpenSession,
                modifier = panelModifier,
            )
        },
    ) { swipeModifier ->
        Scaffold(
            topBar = {
                ControlTopBar(
                    title = summary?.title?.ifBlank { null }
                        ?: uiText("Сессия ${sessionId.takeLast(6)}", "Session ${sessionId.takeLast(6)}"),
                    subtitle = if (connection == "online") uiText("На связи", "Online")
                        else uiText("Восстанавливаем связь…", "Reconnecting…"),
                    connection = connection,
                    work = work,
                    compactStatus = true,
                    showNotifications = false,
                    onMenu = onMenu,
                    actions = {
                        if (selectedMcp != null) Surface(
                            shape = RoundedCornerShape(20.dp),
                            color = MaterialTheme.colorScheme.surfaceVariant,
                            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                        ) {
                            Text("MCP", modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurface)
                        } else IsolationIndicator(isolation)
                        SessionChangesChip(sessionChanges) { changesOpen = true }
                        IconButton(onClick = {
                            notificationsOpen = true
                        }) {
                            BadgedBox(badge = {
                                if (notificationCount > 0) Badge { Text(notificationCount.toString()) }
                            }) {
                                Icon(Icons.Default.Notifications, contentDescription = uiText("Уведомления", "Notifications"))
                            }
                        }
                        IconButton(onClick = { Bridge.openSession(sessionId) }) {
                            Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить", "Refresh"))
                        }
                        IconButton(onClick = { if (mcpRunning) disconnectMcp() else stopMenu = true }, enabled = sessionActive || mcpRunning) {
                            Icon(Icons.Default.Stop, contentDescription = uiText("Остановить", "Stop"))
                        }
                        DropdownMenu(expanded = stopMenu, onDismissRequest = { stopMenu = false }) {
                            DropdownMenuItem(
                                text = { Text(uiText("Мягко остановить сессию", "Cancel session gracefully")) },
                                onClick = {
                                    stopMenu = false
                                    Bridge.cancelSession(sessionId, "cancel")
                                },
                            )
                            DropdownMenuItem(
                                text = { Text(uiText("Остановить сессию жёстко", "Force-stop session")) },
                                onClick = {
                                    stopMenu = false
                                    Bridge.cancelSession(sessionId, "stop")
                                },
                            )
                            DropdownMenuItem(
                                text = { Text(uiText("Аварийно (kill)", "Emergency kill")) },
                                onClick = {
                                    stopMenu = false
                                    Bridge.cancelSession(sessionId, "kill")
                                },
                            )
                        }
                    },
                )
            },
            bottomBar = {
                if (detail?.meta?.get("agent_session")?.toString() == "true") {
                    Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        val parent = (detail?.meta?.get("parent_session_id") as? JsonPrimitive)?.content.orEmpty()
                        val root = (detail?.meta?.get("root_session_id") as? JsonPrimitive)?.content.orEmpty()
                        TextButton(onClick = { if (parent.isNotBlank()) onOpenSession(parent) }) { Text(uiText("← Родитель", "← Parent")) }
                        if (root.isNotBlank() && root != parent) TextButton(onClick = { onOpenSession(root) }) { Text("Main") }
                        TextButton(onClick = { reviewTab = SessionReviewTab.Agents; checkpointsOpen = true }) { Text(uiText("Агенты", "Agents")) }
                    }
                } else Surface(
                    modifier = Modifier.fillMaxWidth().imePadding(),
                    color = MaterialTheme.colorScheme.surface,
                    tonalElevation = 0.dp,
                ) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
                        if (listState.canScrollForward) {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                OutlinedButton(
                                    onClick = {
                                        followTail = false
                                        scope.launch { listState.scrollToItem(0) }
                                    },
                                    enabled = listState.canScrollBackward,
                                    modifier = Modifier.weight(1f),
                                    shape = RoundedCornerShape(12.dp),
                                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.onSurface),
                                ) {
                                    Icon(Icons.Default.ArrowUpward, contentDescription = null, modifier = Modifier.size(18.dp))
                                    Spacer(Modifier.width(6.dp))
                                    Text(uiText("К началу", "To top"), style = MaterialTheme.typography.labelMedium)
                                }
                                OutlinedButton(onClick = {
                                    followTail = true
                                    scope.launch { scrollLatestCard(animate = false) }
                                },
                                    modifier = Modifier.weight(1f),
                                    shape = RoundedCornerShape(12.dp),
                                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.onSurface),
                                ) {
                                    Text(uiText("К последнему", "To latest"), style = MaterialTheme.typography.labelMedium)
                                    Spacer(Modifier.width(6.dp))
                                    Icon(Icons.Default.ArrowDownward, contentDescription = null, modifier = Modifier.size(18.dp))
                                }
                            }
                        }
                        selectedMcp?.let { entry ->
                            Surface(
                                modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                                shape = RoundedCornerShape(12.dp),
                                color = MaterialTheme.colorScheme.surfaceVariant,
                                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                            ) {
                                Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Row(Modifier.weight(1f).clickable(enabled = !mcpRunning) { mcpOpen = true }.padding(vertical = 8.dp),
                                        verticalAlignment = Alignment.CenterVertically) {
                                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                            val server = entry["server"]!!.jsonPrimitive.content
                                            Text((if (server == "gmail") "Gmail" else server) +
                                                (if (entry["readonly"]?.jsonPrimitive?.booleanOrNull == true) " · Только чтение" else ""),
                                                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurface,
                                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                                            Text(if (entry["local"]?.jsonPrimitive?.booleanOrNull == true) "Локальная модель" else "Удалённая модель",
                                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                        Icon(Icons.Default.ArrowDropDown, contentDescription = "Выбрать подключение MCP",
                                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    IconButton(onClick = { disconnectMcp() }) {
                                        Icon(Icons.Default.Close, contentDescription = "Отключить MCP", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                            }
                        }
                        if (selectedSkillIds.isNotEmpty()) {
                            Row(
                                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                            ) {
                                selectedSkillIds.forEach { skillId ->
                                    AssistChip(
                                        onClick = {
                                            if (!sending) selectedSkillIds = selectedSkillIds.filterNot { it == skillId }
                                        },
                                        label = { Text("\$$skillId") },
                                        trailingIcon = { Icon(Icons.Default.Close, contentDescription = uiText("Убрать", "Remove")) },
                                    )
                                }
                            }
                        }
                        if (skillToken != null) {
                            Surface(
                                modifier = Modifier.fillMaxWidth(),
                                shape = RoundedCornerShape(14.dp),
                                color = MaterialTheme.colorScheme.surfaceVariant,
                                tonalElevation = 4.dp,
                            ) {
                                if (skillMatches.isEmpty()) {
                                    Text(
                                        uiText("Подходящих скиллов нет", "No matching skills"),
                                        modifier = Modifier.padding(14.dp),
                                        style = MaterialTheme.typography.bodySmall,
                                    )
                                } else {
                                    Column(Modifier.padding(vertical = 6.dp)) {
                                        skillMatches.forEach { skill ->
                                            Row(
                                                Modifier.fillMaxWidth().clickable {
                                                    selectedSkillIds = selectedSkillIds + skill.skillId
                                                    draft = ""
                                                }.padding(horizontal = 14.dp, vertical = 9.dp),
                                                verticalAlignment = Alignment.CenterVertically,
                                            ) {
                                                Column(Modifier.weight(1f)) {
                                                    Text("\$${skill.name}", style = MaterialTheme.typography.titleSmall)
                                                    Text(
                                                        skill.description.ifBlank { uiText("Описание не указано", "No description") },
                                                        style = MaterialTheme.typography.bodySmall,
                                                        maxLines = 1,
                                                        overflow = TextOverflow.Ellipsis,
                                                    )
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Surface(
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(32.dp),
                            color = MaterialTheme.colorScheme.surfaceVariant,
                            border = BorderStroke(1.dp, if (composerFocused) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)),
                        ) {
                            Column(Modifier.padding(horizontal = 8.dp, vertical = 6.dp)) {
                                if (pending.isNotEmpty()) {
                                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(6.dp),
                                        horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                                        pending.forEach { item ->
                                            if (item.mimeType.startsWith("image/")) {
                                                PendingImagePreview(item.bytes, item.name, !sending) {
                                                    pending = pending.filterNot { it.id == item.id }
                                                }
                                            } else {
                                                AssistChip(onClick = { if (!sending) pending = pending.filterNot { it.id == item.id } },
                                                    label = { Text(item.name, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                                                    trailingIcon = { Icon(Icons.Default.Close, contentDescription = uiText("Убрать", "Remove")) })
                                            }
                                        }
                                    }
                                }
                                BasicTextField(
                                    value = draft,
                                    onValueChange = { draft = it },
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(horizontal = 12.dp, vertical = 8.dp).onFocusChanged { composerFocused = it.isFocused },
                                    textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
                                    cursorBrush = androidx.compose.ui.graphics.SolidColor(MaterialTheme.colorScheme.primary),
                                    maxLines = 5,
                                    decorationBox = { inner ->
                                        Box {
                                            if (draft.isEmpty()) Text(uiText("Сообщение Игорьку", "Message Igorek"), color = MaterialTheme.colorScheme.onSurfaceVariant)
                                            inner()
                                        }
                                    },
                                )
                                Row(
                                    Modifier.fillMaxWidth(),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                                ) {
                                    Box {
                                        IconButton(onClick = { attachMenu = true }, enabled = !sending) {
                                            Icon(Icons.Default.Add, contentDescription = uiText("Прикрепить файл", "Attach file"))
                                        }
                                        DropdownMenu(expanded = attachMenu, onDismissRequest = { attachMenu = false }) {
                                            DropdownMenuItem(text = { Text(uiText("Эмодзи Пепе", "Pepe emoji")) },
                                                onClick = { attachMenu = false; emojiPicker = true })

                                            DropdownMenuItem(
                                                text = { Text(uiText("Подключения MCP", "MCP connections")) },
                                                onClick = { attachMenu = false; mcpOpen = true },
                                            )
                                            DropdownMenuItem(
                                                text = { Text(uiText("Скилл", "Skill")) },
                                                onClick = {
                                                    attachMenu = false
                                                    draft = "\$"
                                                },
                                            )
                                            DropdownMenuItem(
                                                text = { Text(uiText("Фото из галереи", "Photo from gallery")) },
                                                onClick = {
                                                    attachMenu = false
                                                    imagePicker.launch(
                                                        PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
                                                    )
                                                },
                                            )
                                            DropdownMenuItem(
                                                text = { Text(uiText("Документ", "Document")) },
                                                onClick = {
                                                    attachMenu = false
                                                    documentPicker.launch(DOCUMENT_MIME_TYPES)
                                                },
                                            )
                                        }
                                    }
                                    if (selectedMcp != null) Text(
                                        selectedMcp!!["profile"]!!.jsonPrimitive.content,
                                        modifier = Modifier.weight(1f).clickable { if (!mcpRunning) mcpOpen = true }.padding(horizontal = 8.dp, vertical = 12.dp),
                                        style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                        textAlign = TextAlign.End,
                                    ) else SessionProfilePicker(sessionId, profile, composerModel,
                                        busy = sending || sessionActive || streaming || sessionApprovals.isNotEmpty() || currentDetail == null ||
                                            currentDetail.meta["agent_session"]?.jsonPrimitive?.booleanOrNull == true ||
                                            currentDetail.meta["demo"]?.jsonPrimitive?.booleanOrNull == true,
                                        modifier = Modifier.weight(1f))
                                    IconButton(onClick = {
                                        if (androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED) ru.igorek.remote.service.VoiceCalls.open(context, sessionId)
                                        else microphonePermission.launch(android.Manifest.permission.RECORD_AUDIO)
                                    }) { Icon(Icons.Default.GraphicEq, contentDescription = uiText("Голосовой разговор", "Voice call")) }
                                    FilledIconButton(
                                        onClick = {
                                            val text = draft.trim()
                                            val outgoingAttachments = pending
                                            val outgoingSkills = selectedSkillIds
                                            if (text.isEmpty() && outgoingAttachments.isEmpty()) return@FilledIconButton
                                            val mcp = selectedMcp
                                            if (mcp != null) {
                                                if (outgoingAttachments.isNotEmpty() || outgoingSkills.isNotEmpty()) {
                                                    attachError = "Уберите вложения и скиллы для сообщения выбранному MCP."
                                                    return@FilledIconButton
                                                }
                                                if (text.length > 1024) { attachError = "Сократите сообщение MCP до 1024 символов."; return@FilledIconButton }
                                                val current = ++mcpGeneration
                                                val anchor = regularTimeline.size
                                                val prefix = "mcp-" + UUID.randomUUID().toString()
                                                val statusId = "$prefix-status"
                                                updateMcpRows(mcpRows + listOf(
                                                    McpChatRow(anchor, TimelineItem.UserText("$prefix-user", text), server = mcp["server"]!!.jsonPrimitive.content),
                                                    McpChatRow(anchor, TimelineItem.Status(statusId, "MCP: ${mcpConnectionLabel(mcp)} · подключение…"), inProgress = true, server = mcp["server"]!!.jsonPrimitive.content),
                                                ))
                                                draft = ""; mcpRunning = true; attachError = null
                                                mcpJob = scope.launch {
                                                    fun replaceMcpStatus(item: TimelineItem) {
                                                        ru.igorek.remote.data.notificationFailureText(item)?.let {
                                                            Bridge.reportUiError(it, "mcp:$sessionId:${item.id}")
                                                        }
                                                        updateMcpRows(mcpRows.map { if (it.item.id == statusId) it.copy(item = item, inProgress = item is TimelineItem.Status && item.level != "error") else it })
                                                    }
                                                    try {
                                                        mcpClearJob?.join()
                                                        if (current != mcpGeneration) return@launch
                                                        var reply = Bridge.mcpRequest("start", buildJsonObject {
                                                            put("server", mcp["server"]!!.jsonPrimitive.content)
                                                            put("credential_id", mcp["credential_id"]!!.jsonPrimitive.content)
                                                            put("message", text)
                                                        })
                                                        while (current == mcpGeneration) {
                                                            val job = reply["job"]?.jsonObject
                                                            val error = reply["error"]?.jsonPrimitive?.contentOrNull ?: job?.get("error")?.jsonPrimitive?.contentOrNull.orEmpty()
                                                            if (error.isNotBlank()) {
                                                                replaceMcpStatus(TimelineItem.Status(statusId, mcpFailure(error), level = "error")); break
                                                            }
                                                            if (job?.get("running")?.jsonPrimitive?.booleanOrNull != true) {
                                                                replaceMcpStatus(TimelineItem.AssistantText(statusId, job?.get("result")?.jsonPrimitive?.contentOrNull.orEmpty())); break
                                                            }
                                                            replaceMcpStatus(TimelineItem.Status(statusId, "MCP: " + (job["phase"]?.jsonPrimitive?.contentOrNull ?: "работает…")))
                                                            delay(700)
                                                            if (current != mcpGeneration) break
                                                            reply = Bridge.mcpRequest("status")
                                                        }
                                                    } catch (error: Exception) {
                                                        if (current == mcpGeneration) replaceMcpStatus(TimelineItem.Status(statusId, mcpFailure(error), level = "error"))
                                                    } finally { if (current == mcpGeneration) mcpRunning = false }
                                                }
                                                return@FilledIconButton
                                            }
                                            sending = true
                                            attachError = null
                                            scope.launch {
                                                val uploadIds = mutableListOf<String>()
                                                var failure: Throwable? = null
                                                for (item in outgoingAttachments) {
                                                    val result = runCatching { uploadPending(sessionId, item) }
                                                    val uploadId = result.getOrNull()
                                                    if (uploadId == null) {
                                                        failure = result.exceptionOrNull()
                                                        break
                                                    }
                                                    uploadIds += uploadId
                                                    if (item.mimeType.startsWith("image/")) withContext(Dispatchers.IO) {
                                                        runCatching { storeUploadPreview(context, Bridge.serverUrl.value, sessionId, uploadId, item.bytes) }
                                                    }
                                                }
                                                if (failure != null) {
                                                    sending = false
                                                    attachError = failure.message ?: attachmentUploadError
                                                    return@launch
                                                }
                                                draft = ""
                                                pending = emptyList()
                                                selectedSkillIds = emptyList()
                                                Bridge.sendMessage(
                                                    sessionId = sessionId,
                                                    text = text,
                                                    uploadIds = uploadIds,
                                                    attachmentNames = outgoingAttachments.map { it.name },
                                                    uploadedAttachments = outgoingAttachments.zip(uploadIds).map { (item, id) ->
                                                        ru.igorek.remote.data.UploadAttachmentDto(id, item.name, item.bytes.size.toLong(), item.mimeType,
                                                            if (item.mimeType.startsWith("image/")) "image" else "file")
                                                    },
                                                    skillIds = outgoingSkills,
                                                ) { error ->
                                                    sending = false
                                                    if (error != null) {
                                                        draft = if (draft.isBlank()) text else "$text\n$draft"
                                                        pending = outgoingAttachments
                                                        selectedSkillIds = outgoingSkills
                                                    }
                                                }
                                            }
                                        },
                                        enabled = !sending && !switchingProfile && !mcpRunning && (draft.isNotBlank() || pending.isNotEmpty()),
                                        modifier = Modifier.size(48.dp),
                                        shape = CircleShape,
                                        colors = IconButtonDefaults.filledIconButtonColors(
                                            containerColor = MaterialTheme.colorScheme.primary,
                                        ),
                                    ) {
                                        Icon(Icons.AutoMirrored.Filled.Send, contentDescription = uiText("Отправить", "Send"))
                                    }
                                }
                            }
                        }
                    }
                }
            },
        ) { padding ->
            Box(Modifier.fillMaxSize().padding(padding).then(swipeModifier)) {
                Column(Modifier.fillMaxSize()) {
                    if (securityBadgeVisible(securitySettings)) {
                        SecurityWeakenedBanner(securitySettings)
                    }
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(pendingApprovalCards, key = { "pending-${it.taskId}-${it.requestId}" }) { approval ->
                            ApprovalCard(approval, busy = busyApproval != null, onDecide = { decision ->
                                if (busyApproval == null) {
                                    busyApproval = approval.requestId
                                    Bridge.decide(approval, decision) { busyApproval = null }
                                }
                            })
                        }
                        items(ru.igorek.remote.data.projectCharts(timeline), key = { it.id }) { item ->
                            TimelineRow(
                                item = item,
                                sessionId = sessionId,
                                busyRequestId = busyApproval,
                                onDecide = { requestId, taskId, decision ->
                                    if (busyApproval == null) {
                                        busyApproval = requestId
                                        Bridge.decideTool(requestId, taskId, decision) { busyApproval = null }
                                    }
                                },
                                onSendNow = { taskId, messageId ->
                                    Bridge.sendMessageNow(sessionId, taskId, messageId) { error ->
                                        if (error != null) attachError = error.message ?: sendNowError
                                    }
                                },
                                onCancelMessage = { taskId, messageId ->
                                    Bridge.cancelMessage(sessionId, taskId, messageId) { error ->
                                        if (error != null) attachError = error.message ?: sendNowError
                                    }
                                },
                            )
                        }
                        if (timeline.isEmpty()) {
                            item {
                                Column {
                                    Text(
                                        if (detail?.sessionId == sessionId) uiText("Новый чат. Напишите Игорьку.", "New chat. Message Igorek.")
                                        else uiText("Загружаем историю…", "Loading history…"),
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                }
                            }
                        }
                    }
                }

            }
        }
    }

    if (diagnosticSupportOpen) DiagnosticSupportDialog { diagnosticSupportOpen=false;notificationsOpen=true }

    if (notificationsOpen) {
        NotificationCenterDialog(notificationEntries, ::closeNotificationCenter, ::markAllNotificationsRead,
            onClear = Bridge::clearNotifications,
            onSupport = { notificationsOpen = false; diagnosticSupportOpen = true })
    }

    if (changesOpen) {
        SessionChangesDialog(
            changes = sessionChanges,
            onFileClick = { file ->
                diffPath = requestDiffFor(file, sessionId, Bridge::loadSessionChangesDiff)
            },
            onDismiss = { changesOpen = false },
        )
    }

    diffPath?.let { path ->
        DiffDialog(
            path = path,
            diff = sessionChangesDiff,
            onDismiss = {
                diffPath = null
                Bridge.clearSessionChangesDiff()
            },
        )
    }
}

/** Single-error card: text and time of occurrence to determine if it is recent or old. */
@Composable
@OptIn(ExperimentalFoundationApi::class)
internal fun NotificationCard(entry: NotificationEntry) {
    val clipboard=LocalClipboardManager.current
    val context = LocalContext.current
    val copiedMessage = uiText("Скопирован только этот инцидент", "Only this incident was copied")
    val contentColor = if (entry.state == "recovered") MaterialTheme.colorScheme.onSecondaryContainer else MaterialTheme.colorScheme.onErrorContainer
    Surface(
        modifier=Modifier.combinedClickable(onClick={},onLongClickLabel=uiText("Скопировать этот инцидент","Copy this incident"),
            onLongClick={
                clipboard.setText(AnnotatedString(ru.igorek.remote.data.incidentDiagnosticReport(entry, ru.igorek.remote.BuildConfig.VERSION_NAME)))
                Toast.makeText(context, copiedMessage, Toast.LENGTH_SHORT).show()
            }),
        shape = RoundedCornerShape(12.dp),
        color = if(entry.state=="recovered")MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.errorContainer,
    ) {
        Column(
            Modifier.fillMaxWidth().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                entry.text,
                style = MaterialTheme.typography.bodyLarge,
                color = contentColor,
            )
            Text(
                notificationStamp(entry),
                style = MaterialTheme.typography.labelSmall,
                color = contentColor.copy(alpha = 0.78f),
            )
            entry.diagnostic?.let { error ->
                Text(error.code+" · "+entry.id.take(8)+(error.httpRequestId.takeIf { it.isNotBlank() }?.let { "\n"+it } ?: ""),style=MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun notificationStamp(entry: NotificationEntry): String {
    val last = notificationTimeText(if(entry.state=="recovered")entry.recoveredAt ?: entry.lastSeen else entry.lastSeen)
    val status=if(entry.recoveryProof=="operation_confirmed")uiText("Доставка подтверждена","Delivery confirmed") else if(entry.state=="recovered")uiText("Восстановлено","Recovered") else if(entry.diagnostic?.outcome=="unknown")uiText("Результат не подтверждён","Outcome unconfirmed") else ""
    val attempt=entry.diagnostic?.let { error -> if(error.attempt>1 || error.nextRetryAt!=null)
        uiText(" · попытка ${error.attempt}"," · attempt ${error.attempt}")+(error.nextRetryAt?.let { uiText(" · следующая "," · next ")+notificationTimeText(it) } ?: "") else "" }.orEmpty()
    val stamp=(if (entry.read) uiText("Прочитано · ", "Read · ") else "")+(if(status.isNotBlank())status+" · " else "")+last+attempt
    if (entry.count <= 1) return stamp
    val repeats = uiText("событий: ${entry.count}", "events: ${entry.count}")
    val first = notificationTimeText(entry.firstSeen)
    return if (first.isBlank() || first == last) {
        "$stamp · $repeats"
    } else {
        uiText("$stamp · $repeats · первое $first", "$stamp · $repeats · first $first")
    }
}

/** Format: «15:31» for today’s errors and «01.10 15:31» for yesterday’s errors. */
private fun notificationTimeText(ts: Long): String {
    if (ts <= 0L) return ""
    val now = Calendar.getInstance()
    val moment = Calendar.getInstance().apply { timeInMillis = ts }
    val sameDay = now.get(Calendar.YEAR) == moment.get(Calendar.YEAR) &&
        now.get(Calendar.DAY_OF_YEAR) == moment.get(Calendar.DAY_OF_YEAR)
    val pattern = if (sameDay) "HH:mm" else "dd.MM HH:mm"
    return SimpleDateFormat(pattern, Locale.getDefault()).format(Date(ts))
}

@Composable
private fun SecurityWeakenedBanner(settings: SecuritySettingsResponse) {
    val language = LocalAppLanguage.current
    val level = securityLevelLabel(settings.level, language)
    val expiry = securityExpiryText(settings, language)
    val title = uiText("Уровень ослаблен: $level", "Access level weakened: $level")
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.error.copy(alpha = 0.14f),
    ) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Default.Shield,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.error,
                modifier = Modifier.size(18.dp),
            )
            Text(
                text = if (expiry == null) title else "$title · $expiry",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}
