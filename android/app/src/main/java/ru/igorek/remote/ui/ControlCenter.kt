package ru.igorek.remote.ui

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.FolderCopy
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Extension
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.NewReleases
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Badge
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.igorek.remote.BuildConfig
import ru.igorek.remote.R
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ProfileStore
import ru.igorek.remote.data.QuotaState
import ru.igorek.remote.data.QuotaTone
import ru.igorek.remote.data.QuotaWindow
import ru.igorek.remote.data.SessionSummary
import ru.igorek.remote.data.normalizeSecurityLevel
import ru.igorek.remote.data.quotaErrorText
import ru.igorek.remote.data.quotaFreeText
import ru.igorek.remote.data.quotaPercentText
import ru.igorek.remote.data.quotaResetText
import ru.igorek.remote.data.quotaStatusText
import ru.igorek.remote.data.quotaTone
import ru.igorek.remote.data.quotaWindows
import ru.igorek.remote.data.sandboxChangeNeedsConfirmation
import ru.igorek.remote.data.sandboxSwitchEnabled
import ru.igorek.remote.data.securityBadgeVisible
import ru.igorek.remote.data.securityChangeNeedsConfirmation
import ru.igorek.remote.data.securityEffectiveLevel
import ru.igorek.remote.data.securityLevelOptions
import kotlinx.coroutines.delay

internal enum class ControlDestination {
    Sessions,
    Session,
    ExternalSessions,
    Machines,
    Approvals,
    Extensions,
    Artifacts,
    Settings,
    Profile,
}

private data class DrawerEntry(
    val destination: ControlDestination,
    val titleRussian: String,
    val titleEnglish: String,
    val icon: ImageVector,
)

private val drawerEntries = listOf(
    DrawerEntry(ControlDestination.Sessions, "Сессии", "Sessions", Icons.Default.ChatBubbleOutline),
    DrawerEntry(ControlDestination.ExternalSessions, "История агентов", "Agent history", Icons.Default.History),
    DrawerEntry(ControlDestination.Machines, "Машины", "Devices", Icons.Default.Computer),
    DrawerEntry(ControlDestination.Approvals, "Подтверждение команд", "Command approvals", Icons.Default.Shield),
    DrawerEntry(ControlDestination.Extensions, "Расширения", "Extensions", Icons.Default.Extension),
    DrawerEntry(ControlDestination.Artifacts, "Артефакты", "Artifacts", Icons.Default.FolderCopy),
    DrawerEntry(ControlDestination.Settings, "Настройки", "Settings", Icons.Default.Settings),
)

@Composable
internal fun ControlDrawer(
    current: ControlDestination,
    sessions: List<SessionSummary>,
    approvals: Int,
    connection: String,
    selectedSessionId: String?,
    onNavigate: (ControlDestination) -> Unit,
    onOpenSession: (String) -> Unit,
    onProfile: () -> Unit,
) {
    val context = LocalContext.current
    val accent = MaterialTheme.colorScheme.primary
    val octopusColor = remember(accent) {
        ColorFilter.colorMatrix(ColorMatrix(floatArrayOf(
            0f, 0f, 0f, 0f, accent.red * 255f,
            0f, 0f, 0f, 0f, accent.green * 255f,
            0f, 0f, 0f, 0f, accent.blue * 255f,
            0f, 0f, 1.15f, 0f, -16f,
        )))
    }
    ModalDrawerSheet(
        modifier = Modifier.width(304.dp),
        drawerContainerColor = Color(0xFF10182A),
        drawerContentColor = MaterialTheme.colorScheme.onSurface,
    ) {
        Column(Modifier.fillMaxHeight()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 22.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Surface(
                    modifier = Modifier.size(44.dp),
                    shape = RoundedCornerShape(14.dp),
                    color = MaterialTheme.colorScheme.primary.copy(alpha = 0.18f),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Image(
                            painter = painterResource(R.drawable.ic_igorek_octopus),
                            contentDescription = null,
                            modifier = Modifier.fillMaxSize(),
                            colorFilter = octopusColor,
                        )
                    }
                }
                Column {
                    Text("Igorek AI Harness", style = MaterialTheme.typography.titleMedium)
                    Text(
                        if (connection == "online") uiText("Ваш ИИ-агент на связи", "Your AI agent is online")
                        else uiText("Восстанавливаем связь…", "Reconnecting…"),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 12.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                drawerEntries.forEach { entry ->
                    NavigationDrawerItem(
                        label = { Text(uiText(entry.titleRussian, entry.titleEnglish)) },
                        selected = current == entry.destination,
                        onClick = { onNavigate(entry.destination) },
                        icon = { Icon(entry.icon, contentDescription = null) },
                        badge = {
                            if (entry.destination == ControlDestination.Approvals && approvals > 0) {
                                Badge(containerColor = MaterialTheme.colorScheme.error) { Text(approvals.toString()) }
                            }
                        },
                        colors = NavigationDrawerItemDefaults.colors(
                            selectedContainerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.18f),
                            selectedIconColor = MaterialTheme.colorScheme.primary,
                            selectedTextColor = MaterialTheme.colorScheme.onSurface,
                        ),
                    )
                }

                HorizontalDivider(Modifier.padding(vertical = 14.dp), color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
                Text(
                    uiText("НЕДАВНИЕ СЕССИИ", "RECENT SESSIONS"),
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                sessions.take(5).forEach { session ->
                    val sessionColor = when {
                        session.activeStatus == "cancelling" -> MaterialTheme.colorScheme.tertiary
                        session.active -> Color(0xFF44D7A8)
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    }
                    NavigationDrawerItem(
                        label = {
                            Column {
                                Text(
                                    session.title.ifBlank {
                                        uiText("Сессия ${session.sessionId.takeLast(6)}", "Session ${session.sessionId.takeLast(6)}")
                                    },
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    sessionStatusLabel(session, LocalAppLanguage.current),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = sessionColor,
                                )
                            }
                        },
                        selected = current == ControlDestination.Session && selectedSessionId == session.sessionId,
                        onClick = { onOpenSession(session.sessionId) },
                        icon = {
                            Box(
                                Modifier.size(8.dp).background(
                                    sessionColor,
                                    CircleShape,
                                )
                            )
                        },
                        colors = NavigationDrawerItemDefaults.colors(
                            selectedContainerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f),
                        ),
                    )
                }
                if (sessions.isEmpty()) {
                    Text(
                        uiText("Сессии загрузятся после подключения", "Sessions will load after connection"),
                        modifier = Modifier.padding(16.dp),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
            Row(
                Modifier.fillMaxWidth().clickable(onClick = onProfile).padding(18.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                ProfileAvatar(38.dp)
                Column(Modifier.weight(1f)) {
                    Text(uiText("Игорь", "Igor"), fontWeight = FontWeight.SemiBold)
                    Text(
                        uiText("Личный аккаунт", "Personal account"),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(GITHUB_URL)))
                }) {
                    Icon(
                        painter = painterResource(R.drawable.ic_github),
                        contentDescription = uiText("Открыть GitHub", "Open GitHub"),
                    )
                }
            }
        }
    }
}

// Deliberately diverges from connectionDotColor: any connection problem is red, never amber.
internal fun connectionStatusColor(connection: String?): Color =
    if (connection == "online") Success else Danger

internal fun connectionStatusLabel(
    connection: String?,
    fallback: String? = null,
    language: AppLanguage = AppLocale.current,
): String {
    fallback?.takeIf { it.isNotBlank() }?.let { return it }
    val online = connection == "online"
    return if (language == AppLanguage.Russian) {
        if (online) "На связи" else "Нет соединения"
    } else {
        if (online) "Online" else "Offline"
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ControlTopBar(
    title: String,
    subtitle: String? = null,
    connection: String? = null,
    work: WorkPhase = WorkPhase.Idle,
    compactStatus: Boolean = false,
    onMenu: () -> Unit,
    actions: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit = {},
) {
    Column(Modifier.fillMaxWidth()) {
        TopAppBar(
            title = {
                if (compactStatus) {
                    CompactConnectionStatus(title, subtitle, connection, work)
                } else {
                    Column(Modifier.fillMaxWidth()) {
                        Row(
                            Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(7.dp),
                        ) {
                            Text(
                                title,
                                modifier = Modifier.weight(1f, fill = false),
                                style = MaterialTheme.typography.titleMedium,
                                maxLines = 1,
                                softWrap = false,
                                overflow = TextOverflow.Ellipsis,
                            )
                            if (work != WorkPhase.Idle) {
                                WorkStatusDot(work)
                            } else {
                                connection?.let {
                                    Box(Modifier.size(8.dp).background(connectionDotColor(it), CircleShape))
                                }
                            }
                        }
                        subtitle?.let {
                            Text(
                                it,
                                modifier = Modifier.fillMaxWidth(),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                softWrap = false,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            },
            navigationIcon = {
                IconButton(onClick = onMenu) {
                    Icon(Icons.Default.Menu, contentDescription = uiText("Открыть меню", "Open menu"))
                }
            },
            actions = actions,
            colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
        )
        if (work != WorkPhase.Idle) {
            WorkSweepLine(work)
        }
    }
}

@Composable
private fun CompactConnectionStatus(
    title: String,
    subtitle: String?,
    connection: String?,
    work: WorkPhase,
) {
    var open by remember { mutableStateOf(false) }
    val statusColor = connectionStatusColor(connection)
    val statusText = connectionStatusLabel(connection, subtitle)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
        Box {
            Box(
                Modifier
                    .size(27.dp)
                    .clip(CircleShape)
                    .background(statusColor)
                    .clickable { open = true }
                    .semantics { contentDescription = "$title — $statusText" },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "i",
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = FontWeight.Bold,
                    color = Color.White,
                )
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                SessionStatusCard(
                    title = title,
                    statusText = statusText,
                    statusColor = statusColor,
                    work = work,
                )
            }
        }
        if (work != WorkPhase.Idle) {
            WorkStatusDot(work)
        }
    }
}

// Compact card in app style: current session name, work status, and OpenCode subscription quota (5 hours/week/month)—same numbers as shown in the web panel. Opens via a tap on the circle with a letter in the header.
@Composable
private fun SessionStatusCard(
    title: String,
    statusText: String,
    statusColor: Color,
    work: WorkPhase,
) {
    val quotaState by Bridge.quota.collectAsState()
    // The only loading trigger is card opening: no requests without a tap.
    LaunchedEffect(Unit) { Bridge.refreshQuota() }
    Column(
        Modifier.widthIn(min = 240.dp, max = 300.dp).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            title,
            style = MaterialTheme.typography.titleSmall,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.size(8.dp).background(statusColor, CircleShape))
            Text(
                statusText,
                style = MaterialTheme.typography.labelMedium,
                color = statusColor,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        workPhaseLabel(work)?.let {
            Text(
                it,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        HorizontalDivider(
            Modifier.padding(top = 4.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f),
        )
        QuotaSection(quotaState)
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
        ContextSection()
    }
}

@Composable
private fun ContextSection() {
    val usage by Bridge.contextUsage.collectAsState()
    val detail by Bridge.sessionDetail.collectAsState()
    val window = usage.window ?: detail?.contextWindow
    val percent = usage.usedPercent(window)
    val locale = if (LocalAppLanguage.current == AppLanguage.Russian) java.util.Locale.forLanguageTag("ru") else java.util.Locale.US
    val formatter = java.text.NumberFormat.getIntegerInstance(locale)
    fun count(value: Long) = formatter.format(value)
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(uiText("Контекстное окно", "Context window"), style = MaterialTheme.typography.labelLarge)
        if (percent != null && window != null && usage.lastInput != null) {
            val free = (window - usage.lastInput!!).coerceAtLeast(0)
            val color = quotaToneColor(quotaTone(percent.toDouble(), ""))
            Text(uiText("Свободно ${maxOf(0, 100 - percent)}%", "${maxOf(0, 100 - percent)}% free"), color = color, style = MaterialTheme.typography.bodySmall)
            Box(Modifier.fillMaxWidth().height(4.dp).background(MaterialTheme.colorScheme.surfaceVariant)) {
                Box(Modifier.fillMaxWidth(percent.coerceIn(0, 100) / 100f).fillMaxHeight().background(color))
            }
            Text(uiText("Осталось ${count(free)} из ${count(window)} токенов", "${count(free)} of ${count(window)} tokens remaining"), style = MaterialTheme.typography.bodySmall)
            Text(uiText("Занято ${count(usage.lastInput!!)} токенов ($percent%)", "${count(usage.lastInput!!)} tokens used ($percent%)"), style = MaterialTheme.typography.labelSmall)
        } else {
            window?.let { Text(uiText("Размер: ${count(it)} токенов", "Size: ${count(it)} tokens"), style = MaterialTheme.typography.bodySmall) }
            usage.lastInput?.let { Text(uiText("Последний запрос: ${count(it)} токенов", "Last request: ${count(it)} tokens"), style = MaterialTheme.typography.bodySmall) }
            Text(uiText(if (usage.lastInput == null) "Данных о токенах пока нет" else "Размер окна пока недоступен", if (usage.lastInput == null) "No token data yet" else "Context window size unavailable"), style = MaterialTheme.typography.labelSmall)
        }
        Text(uiText("По последнему запросу к модели", "Based on the last model request"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun QuotaSection(state: QuotaState) {
    val language = LocalAppLanguage.current
    Column(
        Modifier.padding(top = 2.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                uiText("Квота подписки", "Subscription quota"),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            IconButton(
                onClick = { Bridge.refreshQuota(force = true) },
                modifier = Modifier.size(28.dp),
            ) {
                Icon(
                    Icons.Default.Refresh,
                    contentDescription = uiText("Обновить квоту", "Refresh quota"),
                    modifier = Modifier.size(16.dp),
                )
            }
        }
        when (state) {
            QuotaState.Idle, QuotaState.Loading -> Text(
                uiText("Загружаю…", "Loading…"),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            is QuotaState.Failed -> Text(
                quotaErrorText(state.code, language),
                style = MaterialTheme.typography.labelSmall,
                color = Danger,
            )
            is QuotaState.Ready -> QuotaRows(state)
        }
    }
}

@Composable
private fun QuotaRows(state: QuotaState.Ready) {
    val language = LocalAppLanguage.current
    val now = System.currentTimeMillis()
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (window in quotaWindows(state.usage, state.provider)) {
            QuotaRowRow(window, language, now)
        }
        Text(
            uiText(
                if (state.provider == "codex") "Недельная квота Codex выбранного аккаунта ChatGPT." else "Квота подписки OpenCode Go, с которой работает этот компьютер.",
                if (state.provider == "codex") "Weekly Codex quota of the selected ChatGPT account." else "OpenCode Go subscription quota used by this computer.",
            ),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun QuotaRowRow(window: QuotaWindow, language: AppLanguage, nowMillis: Long) {
    val toneColor = quotaToneColor(quotaTone(window.percent, window.status))
    val meta = listOfNotNull(
        quotaFreeText(window.percent).takeIf { it.isNotEmpty() },
        quotaResetText(window.resetsAt, nowMillis, language).takeIf { it.isNotEmpty() },
        quotaStatusText(window.status, language).takeIf { it.isNotEmpty() },
    ).joinToString(" · ")
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (language == AppLanguage.Russian) window.kind.labelRu else window.kind.labelEn,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                quotaPercentText(window.percent),
                style = MaterialTheme.typography.bodySmall,
                fontWeight = FontWeight.SemiBold,
                color = toneColor,
            )
        }
        window.percent?.let { percent ->
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(4.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Box(
                    Modifier
                        .fillMaxWidth(percent.coerceIn(0.0, 100.0).toFloat() / 100f)
                        .fillMaxHeight()
                        .background(toneColor),
                )
            }
        }
        if (meta.isNotEmpty()) {
            Text(
                meta,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

internal fun quotaToneColor(tone: QuotaTone): Color = when (tone) {
    QuotaTone.Ok -> Success
    QuotaTone.Warn -> Warning
    QuotaTone.Danger -> Danger
}

@Composable
internal fun ApprovalCenterScreen(onMenu: () -> Unit) {
    val approvals by Bridge.approvals.collectAsState()
    var busy by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { Bridge.refreshApprovals() }
    Scaffold(topBar = {
        ControlTopBar(
            uiText("Подтверждение команд", "Command approvals"),
            uiText("Опасные действия и запросы доступа", "Dangerous actions and access requests"),
            onMenu = onMenu,
        )
    }) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(approvals, key = { "${it.taskId}-${it.requestId}" }) { approval ->
                ApprovalCard(approval, busy = busy != null, onDecide = { decision ->
                    if (busy == null) {
                        busy = approval.requestId
                        if (approval.kind == "sudo") {
                            Bridge.decideSudo(approval.requestId, decision, "") { busy = null }
                        } else {
                            Bridge.decide(approval, decision) { busy = null }
                        }
                    }
                }, onSudo = { password ->
                    if (busy == null) {
                        busy = approval.requestId
                        Bridge.decideSudo(approval.requestId, "allow_once", password) { busy = null }
                    }
                })
            }
            if (approvals.isEmpty()) {
                item {
                    EmptyControlState(
                        Icons.Default.Shield,
                        uiText("Нет ожидающих подтверждений", "No pending approvals"),
                        uiText("Новые запросы появятся здесь и внутри сессии.", "New requests will appear here and inside the session."),
                    )
                }
            }
        }
    }
}

@Composable
internal fun ArtifactsScreen(sessionId: String?, onMenu: () -> Unit) {
    val files by Bridge.attachments.collectAsState()
    val sessions by Bridge.sessions.collectAsState()
    val target = sessionId ?: sessions.firstOrNull()?.sessionId
    LaunchedEffect(target) {
        target?.let {
            Bridge.openSession(it)
            Bridge.refreshFiles(it)
        }
    }
    Scaffold(topBar = {
        ControlTopBar(
            uiText("Артефакты", "Artifacts"),
            target?.let { uiText("Сессия ${it.takeLast(6)}", "Session ${it.takeLast(6)}") },
            onMenu = onMenu,
        )
    }) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (target != null) {
                items(files, key = { it.id }) { file -> AttachmentCard(target, file) }
            }
            if (target == null || files.isEmpty()) {
                item {
                    EmptyControlState(
                        Icons.Default.FolderCopy,
                        uiText("Артефактов пока нет", "No artifacts yet"),
                        uiText("Созданные Игорьком файлы появятся здесь.", "Files created by Igorek will appear here."),
                    )
                }
            }
        }
    }
}

@Composable
internal fun SettingsScreen(onMenu: () -> Unit, onMachines: () -> Unit) {
    val context = LocalContext.current
    val screenshotsBlocked by Bridge.screenshotsBlocked.collectAsState()
    var mcpSettingsOpen by remember { mutableStateOf(false) }
    var whatsNewOpen by remember { mutableStateOf(false) }
    if (whatsNewOpen) WhatsNewDialog(onDismiss = { whatsNewOpen = false })
    if (mcpSettingsOpen) McpTaskDialog(onDismiss = { mcpSettingsOpen = false }, initialManage = true)
    val serverUrl by Bridge.serverUrl.collectAsState()
    val savedConnections by Bridge.savedConnections.collectAsState()
    var scanConnection by remember { mutableStateOf(false) }
    var addConnection by remember { mutableStateOf(false) }
    var connectionBusy by remember { mutableStateOf(false) }
    var connectionError by remember { mutableStateOf<String?>(null) }
    if (addConnection) androidx.compose.ui.window.Dialog(onDismissRequest = { addConnection = false }, properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false)) { PairingScreen(onDone = { addConnection = false }, onCancel = { addConnection = false }, scanImmediately = scanConnection) }
    val language by Bridge.language.collectAsState()
    val approvalsEnabled by Bridge.approvalsEnabled.collectAsState()
    val securitySettings by Bridge.securitySettings.collectAsState()
    val sandboxSettings by Bridge.sandboxSettings.collectAsState()
    var addressDialog by remember { mutableStateOf(false) }
    var approvalBusy by remember { mutableStateOf(false) }
    var approvalError by remember { mutableStateOf<String?>(null) }
    var securityBusy by remember { mutableStateOf(false) }
    var securityError by remember { mutableStateOf<String?>(null) }
    var securityRefreshError by remember { mutableStateOf<String?>(null) }
    var securityPending by remember { mutableStateOf<String?>(null) }
    var sandboxBusy by remember { mutableStateOf(false) }
    var sandboxError by remember { mutableStateOf<String?>(null) }
    var sandboxRefreshError by remember { mutableStateOf<String?>(null) }
    var sandboxConfirm by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { Bridge.refreshApprovalSettings() }
    // The badge's expiration is limited by TTL: if the refresh period for the badge is skipped, the expiration date will remain outdated once returning to the baseline level.
    LaunchedEffect(Unit) {
        while (true) {
            Bridge.refreshSecuritySettings { error -> securityRefreshError = error?.message }
            Bridge.refreshSandboxSettings { error -> sandboxRefreshError = error?.message }
            delay(60_000)
        }
    }
    if (addressDialog) ServerAddressDialog(serverUrl) { addressDialog = false }
    Scaffold(topBar = {
        ControlTopBar(
            uiText("Настройки", "Settings"),
            uiText("Подключение и безопасность", "Connection and security"),
            onMenu = onMenu,
        )
    }) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val actionShape = RoundedCornerShape(12.dp)
            val actionBorder = BorderStroke(1.dp, MaterialTheme.colorScheme.primary)
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { mcpSettingsOpen = true }, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(0.dp), contentPadding = PaddingValues(vertical = 4.dp)) {
                        Text(uiText("Подключения MCP и доступ моделей", "MCP connections and model access"), modifier = Modifier.fillMaxWidth(), style = MaterialTheme.typography.titleMedium)
                    }
                    Text(uiText("Подключения", "Connections"), style = MaterialTheme.typography.titleSmall)
                    savedConnections.forEach { saved ->
                        val selectionState = if (saved.active) uiText("Текущее подключение", "Current connection") else uiText("Не выбрано", "Not selected")
                        OutlinedButton(onClick = {
                            connectionBusy = true; connectionError = null
                            Bridge.switchConnection(saved.id) { error -> connectionBusy = false; connectionError = error?.message }
                        }, enabled = !connectionBusy && !saved.active,
                            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).semantics { stateDescription = selectionState },
                            shape = actionShape,
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                            colors = ButtonDefaults.outlinedButtonColors(
                                contentColor = MaterialTheme.colorScheme.onSurface,
                                disabledContentColor = MaterialTheme.colorScheme.onSurface,
                            ),
                            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline),
                        ) {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                if (saved.active) Icon(Icons.Default.Check, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(saved.label, style = MaterialTheme.typography.titleSmall)
                                    Text(saved.url, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                                Box(Modifier.size(8.dp).background(if (saved.active) Color(0xFF44D7A8) else Color(0xFFFF667A), CircleShape))
                            }
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(top = 6.dp).height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = { scanConnection = true; addConnection = true },
                            enabled = !connectionBusy,
                            modifier = Modifier.weight(1f).heightIn(min = 52.dp).fillMaxHeight(),
                            shape = actionShape,
                            border = actionBorder,
                            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 8.dp),
                        ) {
                            Icon(Icons.Default.QrCodeScanner, contentDescription = null, modifier = Modifier.size(22.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(uiText("Подключить\nпо QR-коду", "Connect by\nQR code"), modifier = Modifier.weight(1f))
                        }
                        OutlinedButton(
                            onClick = { scanConnection = false; addConnection = true },
                            enabled = !connectionBusy,
                            modifier = Modifier.weight(1f).heightIn(min = 52.dp).fillMaxHeight(),
                            shape = actionShape,
                            border = actionBorder,
                            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 8.dp),
                        ) {
                            Icon(Icons.Default.Add, contentDescription = null, modifier = Modifier.size(22.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(uiText("Добавить\nподключение", "Add\nconnection"), modifier = Modifier.weight(1f))
                        }
                    }
                    connectionError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    HorizontalDivider(Modifier.padding(vertical = 6.dp), color = MaterialTheme.colorScheme.outline)
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(uiText("Сервер", "Server"), style = MaterialTheme.typography.titleSmall)
                            Text(serverUrl, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        OutlinedButton(onClick = { addressDialog = true }, shape = actionShape, border = actionBorder, contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp)) {
                            Text(uiText("Изменить адрес", "Change address"))
                        }
                    }
                }
            }
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(uiText("Запретить скриншоты", "Block screenshots"), style = MaterialTheme.typography.titleSmall)
                        Text(
                            uiText("Блокирует снимки и запись экрана во всём приложении, включая MCP. По умолчанию выключено.",
                                "Blocks screenshots and screen recording throughout the app, including MCP. Off by default."),
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    Switch(checked = screenshotsBlocked, onCheckedChange = Bridge::setScreenshotsBlocked)
                }
            }
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(uiText("Подтверждение команд", "Command approvals"), style = MaterialTheme.typography.titleSmall)
                    Text(
                        if (approvalsEnabled) {
                            uiText(
                                "Включено: опасные действия в мобильных сессиях требуют подтверждения.",
                                "Enabled: risky actions in mobile sessions require confirmation.",
                            )
                        } else {
                            uiText(
                                "Отключено: опасные действия в мобильных сессиях выполняются без запроса.",
                                "Disabled: risky actions in mobile sessions run without prompting.",
                            )
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = if (approvalsEnabled) MaterialTheme.colorScheme.onSurfaceVariant
                        else MaterialTheme.colorScheme.error,
                    )
                    approvalError?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (approvalsEnabled) {
                            Button(onClick = {}, enabled = false, modifier = Modifier.weight(1f), shape = actionShape) {
                                Text(uiText("Включить", "Enable"))
                            }
                        } else {
                            OutlinedButton(
                                onClick = {
                                    approvalBusy = true
                                    approvalError = null
                                    Bridge.setApprovalsEnabled(true) { error ->
                                        approvalBusy = false
                                        approvalError = error?.message
                                    }
                                },
                                enabled = !approvalBusy,
                                modifier = Modifier.weight(1f),
                                shape = actionShape,
                                border = actionBorder,
                            ) { Text(uiText("Включить", "Enable")) }
                        }
                        if (!approvalsEnabled) {
                            Button(onClick = {}, enabled = false, modifier = Modifier.weight(1f), shape = actionShape) {
                                Text(uiText("Отключить", "Disable"))
                            }
                        } else {
                            OutlinedButton(
                                onClick = {
                                    approvalBusy = true
                                    approvalError = null
                                    Bridge.setApprovalsEnabled(false) { error ->
                                        approvalBusy = false
                                        approvalError = error?.message
                                    }
                                },
                                enabled = !approvalBusy,
                                modifier = Modifier.weight(1f),
                                shape = actionShape,
                                border = actionBorder,
                            ) { Text(uiText("Отключить", "Disable")) }
                        }
                    }
                }
            }
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    val currentLevel = securityEffectiveLevel(securitySettings)
                    val weakened = securityBadgeVisible(securitySettings)
                    Text(uiText("Уровень доступа", "Access level"), style = MaterialTheme.typography.titleSmall)
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            uiText("Текущий уровень: ", "Current level: ") +
                                securityLevelLabel(currentLevel, LocalAppLanguage.current),
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.SemiBold,
                        )
                        if (weakened) {
                            Surface(
                                color = MaterialTheme.colorScheme.error.copy(alpha = 0.16f),
                                shape = RoundedCornerShape(50),
                            ) {
                                Text(
                                    uiText("ослаблен", "weakened"),
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.error,
                                )
                            }
                        }
                    }
                    securityExpiryText(securitySettings, LocalAppLanguage.current)?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    }
                    Text(
                        when {
                            currentLevel == "full" -> uiText(
                                "Опасно: защита отключена полностью до истечения срока.",
                                "Danger: protection is fully disabled until the level expires.",
                            )
                            weakened -> uiText(
                                "Уровень ослаблен: часть ограничений снята до истечения срока.",
                                "The level is weakened: some restrictions are lifted until it expires.",
                            )
                            else -> uiText(
                                "Усиление применяется сразу, ослабление — только после подтверждения и на ограниченное время.",
                                "Strengthening applies immediately; weakening requires confirmation and is time-limited.",
                            )
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = if (weakened) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    (securityError ?: securityRefreshError)?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        securityLevelOptions(securitySettings).forEach { level ->
                            if (level == currentLevel) {
                                Button(onClick = {}, enabled = false, modifier = Modifier.fillMaxWidth()) {
                                    Text(
                                        securityLevelLabel(level, LocalAppLanguage.current) +
                                            uiText(" · текущий", " · current"),
                                    )
                                }
                            } else {
                                OutlinedButton(
                                    onClick = {
                                        if (securityChangeNeedsConfirmation(level, securitySettings)) {
                                            securityPending = level
                                        } else {
                                            securityBusy = true
                                            securityError = null
                                            Bridge.setSecurityLevel(level) { error ->
                                                securityBusy = false
                                                securityError = error?.message
                                            }
                                        }
                                    },
                                    enabled = !securityBusy,
                                    modifier = Modifier.fillMaxWidth(),
                                ) { Text(securityLevelLabel(level, LocalAppLanguage.current)) }
                            }
                        }
                    }
                }
            }
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(uiText("Песочница Bash", "Bash sandbox"), style = MaterialTheme.typography.titleSmall)
                            Text(
                                sandboxStatusLabel(sandboxSettings, LocalAppLanguage.current),
                                style = MaterialTheme.typography.bodySmall,
                                color = if (sandboxSettings.active) MaterialTheme.colorScheme.onSurfaceVariant
                                else MaterialTheme.colorScheme.error,
                            )
                        }
                        Switch(
                            checked = sandboxSettings.enabled,
                            onCheckedChange = { enabled ->
                                if (sandboxChangeNeedsConfirmation(enabled, sandboxSettings)) {
                                    sandboxConfirm = true
                                } else {
                                    sandboxBusy = true
                                    sandboxError = null
                                    Bridge.setSandboxEnabled(enabled) { error ->
                                        sandboxBusy = false
                                        sandboxError = error?.message
                                    }
                                }
                            },
                            enabled = sandboxSwitchEnabled(sandboxSettings) && !sandboxBusy,
                        )
                    }
                    Text(
                        uiText(
                            "Включение применяется сразу, отключение — только после подтверждения.",
                            "Enabling applies immediately; disabling requires confirmation.",
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    (sandboxError ?: sandboxRefreshError)?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    }
                }
            }
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(uiText("Язык интерфейса", "Interface language"), style = MaterialTheme.typography.titleSmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (language == AppLanguage.Russian) {
                            Button(onClick = {}, modifier = Modifier.weight(1f)) { Text("Русский") }
                        } else {
                            OutlinedButton(
                                onClick = { Bridge.setLanguage(AppLanguage.Russian) },
                                modifier = Modifier.weight(1f),
                            ) { Text("Русский") }
                        }
                        if (language == AppLanguage.English) {
                            Button(onClick = {}, modifier = Modifier.weight(1f)) { Text("English") }
                        } else {
                            OutlinedButton(
                                onClick = { Bridge.setLanguage(AppLanguage.English) },
                                modifier = Modifier.weight(1f),
                            ) { Text("English") }
                        }
                    }
                }
            }
            OutlinedButton(onClick = onMachines, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Default.Computer, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text(uiText("Управление устройствами", "Manage devices"))
            }
            OutlinedButton(onClick = { Bridge.unpair() }, modifier = Modifier.fillMaxWidth()) {
                Text(uiText("Отключить это устройство", "Disconnect this device"), color = MaterialTheme.colorScheme.error)
            }
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        Modifier.weight(1f).clickable {
                            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(GITHUB_URL)))
                        }.padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(
                            painter = painterResource(R.drawable.ic_github),
                            contentDescription = null,
                            modifier = Modifier.size(28.dp),
                        )
                        Column(Modifier.weight(1f)) {
                            Text("GitHub · Endorpheen", style = MaterialTheme.typography.titleSmall)
                            Text(
                                "Igorek Remote ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    IconButton(onClick = { whatsNewOpen = true }, modifier = Modifier.padding(end = 8.dp)) {
                        Icon(Icons.Default.NewReleases, contentDescription = uiText("Что нового", "What's new"), tint = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
    }
    securityPending?.let { target ->
        SecurityWeakenDialog(
            target = target,
            current = securityEffectiveLevel(securitySettings),
            busy = securityBusy,
            onConfirm = {
                securityBusy = true
                securityError = null
                Bridge.setSecurityLevel(target, confirm = true) { error ->
                    securityBusy = false
                    securityPending = null
                    securityError = error?.message
                }
            },
            onDismiss = { if (!securityBusy) securityPending = null },
        )
    }
    if (sandboxConfirm) {
        AlertDialog(
            onDismissRequest = { if (!sandboxBusy) sandboxConfirm = false },
            icon = { Icon(Icons.Default.Shield, contentDescription = null, tint = MaterialTheme.colorScheme.error) },
            title = { Text(uiText("Отключить песочницу?", "Disable the sandbox?")) },
            text = {
                Text(
                    uiText(
                        "Агент сможет писать за пределы разрешённых папок.",
                        "The agent will be able to write outside the allowed folders.",
                    ),
                    style = MaterialTheme.typography.bodyMedium,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        sandboxBusy = true
                        sandboxError = null
                        Bridge.setSandboxEnabled(false) { error ->
                            sandboxBusy = false
                            sandboxConfirm = false
                            sandboxError = error?.message
                        }
                    },
                    enabled = !sandboxBusy,
                ) { Text(uiText("Отключить", "Disable"), color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { sandboxConfirm = false }, enabled = !sandboxBusy) {
                    Text(uiText("Отмена", "Cancel"))
                }
            },
        )
    }
}

@Composable
private fun SecurityWeakenDialog(
    target: String,
    current: String,
    busy: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val language = LocalAppLanguage.current
    val targetLabel = securityLevelLabel(target, language)
    val fullTarget = normalizeSecurityLevel(target) == "full"
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(Icons.Default.Shield, contentDescription = null, tint = MaterialTheme.colorScheme.error) },
        title = { Text(uiText("Ослабить защиту?", "Weaken protection?")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    uiText(
                        "Уровень: ${securityLevelLabel(current, language)} → $targetLabel.",
                        "Level: ${securityLevelLabel(current, language)} → $targetLabel.",
                    ),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    if (fullTarget) {
                        uiText(
                            "Внимание: полный уровень отключает защиту — guard выключен, security disabled, агенту доступен полный файловый доступ.",
                            "Warning: the full level disables protection — guard off, security disabled, full filesystem access for the agent.",
                        )
                    } else {
                        uiText(
                            "Часть ограничений будет снята на время действия уровня.",
                            "Some restrictions will be lifted while the level is active.",
                        )
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
                Text(
                    uiText(
                        "Ослабление ограничено по времени (по умолчанию 1 час), затем вернётся базовый уровень.",
                        "The weakening is time-limited (1 hour by default), then the baseline level returns.",
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onConfirm, enabled = !busy) {
                Text(uiText("Подтвердить ослабление", "Confirm weakening"), color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !busy) {
                Text(uiText("Отмена", "Cancel"))
            }
        },
    )
}

private const val GITHUB_URL = "https://github.com/Endorpheen"

@Composable
internal fun ProfileAvatar(size: androidx.compose.ui.unit.Dp) {
    val context = LocalContext.current
    val revision by ProfileStore.revision.collectAsState()
    val bitmap = remember(revision) {
        ProfileStore.avatarFile(context).takeIf { it.isFile }?.let { BitmapFactory.decodeFile(it.absolutePath) }
    }
    Surface(
        modifier = Modifier.size(size),
        shape = CircleShape,
        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f),
    ) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap.asImageBitmap(),
                contentDescription = uiText("Аватар Игоря", "Igor's avatar"),
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        } else {
            Box(contentAlignment = Alignment.Center) {
                Icon(
                    Icons.Default.AccountCircle,
                    contentDescription = null,
                    modifier = Modifier.fillMaxSize(),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
internal fun ProfileScreen(onMenu: () -> Unit, onOpenSession: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            busy = true
            error = null
            scope.launch {
                runCatching { ProfileStore.saveAvatar(context, uri) }
                    .onFailure { error = it.message }
                busy = false
            }
        }
    }
    Scaffold(topBar = {
        ControlTopBar(
            uiText("Профиль", "Profile"),
            uiText("Личный аккаунт", "Personal account"),
            onMenu = onMenu,
        )
    }) { padding ->
        SchedulesPanel(Modifier.fillMaxSize().padding(padding), onOpenSession = onOpenSession, profileHeader = {
            Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                ProfileAvatar(64.dp)
                Column(Modifier.weight(1f)) {
                    Text(uiText("Игорь", "Igor"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    Text(uiText("Личный аккаунт", "Personal account"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                IconButton(onClick = { picker.launch("image/*") }, enabled = !busy) {
                    if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    else Icon(Icons.Default.Edit, uiText("Выбрать аватарку", "Choose avatar"), tint = MaterialTheme.colorScheme.primary)
                }
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        })
    }
}

@Composable
private fun EmptyControlState(icon: ImageVector, title: String, text: String) {
    Column(
        Modifier.fillMaxWidth().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(42.dp), tint = MaterialTheme.colorScheme.primary)
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
