package ru.igorek.remote.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import ru.igorek.remote.data.PairingQr
import ru.igorek.remote.data.decodePairingQr
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Archive
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.DeleteForever
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import androidx.compose.runtime.rememberCoroutineScope
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.ArchiveSessionsResponse
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.DeviceDto
import ru.igorek.remote.data.SessionSummary
import ru.igorek.remote.data.SessionOptionsResponse

internal fun chatCountLabel(count: Int, language: AppLanguage = AppLocale.current): String {
    if (language == AppLanguage.English) return "$count ${if (count == 1) "chat" else "chats"}"
    val modulo100 = count % 100
    val modulo10 = count % 10
    val noun = when {
        modulo100 in 11..14 -> "чатов"
        modulo10 == 1 -> "чат"
        modulo10 in 2..4 -> "чата"
        else -> "чатов"
    }
    return "$count $noun"
}

internal fun archiveResultText(result: ArchiveSessionsResponse): String =
    if (result.skippedActive > 0) {
        AppLocale.text(
            "Архивировано ${chatCountLabel(result.updated)}. Активных пропущено: ${result.skippedActive}.",
            "Archived ${chatCountLabel(result.updated)}. Active chats skipped: ${result.skippedActive}.",
        )
    } else {
        AppLocale.text("Архивировано ${chatCountLabel(result.updated)}.", "Archived ${chatCountLabel(result.updated)}.")
    }

internal fun sessionStatusLabel(session: SessionSummary, language: AppLanguage = AppLocale.current): String {
    val status = session.activeStatus.ifBlank { session.lastTaskStatus }.lowercase()
    fun pick(russian: String, english: String) = if (language == AppLanguage.Russian) russian else english
    return when {
        session.pendingApprovals > 0 || status == "waiting_approval" -> pick("ждёт подтверждения", "waiting for approval")
        status == "cancelling" -> pick("останавливается", "stopping")
        status == "queued" -> pick("в очереди", "queued")
        status == "processing" || status == "retry" -> pick("работает", "running")
        status == "ok" -> pick("готово", "complete")
        status == "fail" -> pick("завершена", "finished")
        status == "dead_letter" || status == "cancelled" -> pick("остановлена", "stopped")
        status == "timeout" -> pick("завершена", "finished")
        status.isNotBlank() -> status
        else -> pick("готова", "ready")
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun SessionOptionField(
    label: String,
    value: String,
    options: List<String>,
    enabled: Boolean,
    onValueChange: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { if (enabled && options.isNotEmpty()) expanded = !expanded },
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.fillMaxWidth().menuAnchor(),
            label = { Text(label) },
            singleLine = true,
            enabled = enabled,
            trailingIcon = {
                ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded)
            },
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option) },
                    onClick = {
                        onValueChange(option)
                        expanded = false
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot() {
    val language by Bridge.language.collectAsState()
    CompositionLocalProvider(LocalAppLanguage provides language) {
        AppRootContent()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AppRootContent() {
    val paired by Bridge.paired.collectAsState()
    if (!paired) {
        PairingScreen()
        return
    }

    val sessions by Bridge.sessions.collectAsState()
    val approvals by Bridge.approvals.collectAsState()
    val connection by Bridge.connection.collectAsState()
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    var destinationName by rememberSaveable { mutableStateOf(ControlDestination.Sessions.name) }
    val serverScope by Bridge.serverUrl.collectAsState()
    var selectedSessionId by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingSkillId by remember { mutableStateOf<String?>(null) }
    val destination = runCatching { ControlDestination.valueOf(destinationName) }
        .getOrDefault(ControlDestination.Sessions)
    val openMenu = { scope.launch { drawerState.open() }; Unit }

    LaunchedEffect(serverScope) {
        selectedSessionId = null; pendingSkillId = null
        if (destinationName == ControlDestination.Session.name) destinationName = ControlDestination.Sessions.name
        Bridge.refreshSessions()
        Bridge.refreshApprovals()
    }

    fun navigate(target: ControlDestination) {
        destinationName = target.name
        scope.launch { drawerState.close() }
    }

    fun openSession(sessionId: String) {
        selectedSessionId = sessionId
        destinationName = ControlDestination.Session.name
        scope.launch { drawerState.close() }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ControlDrawer(
                current = destination,
                sessions = sessions,
                approvals = approvals.size,
                connection = connection,
                selectedSessionId = selectedSessionId,
                onNavigate = ::navigate,
                onOpenSession = ::openSession,
                onProfile = { navigate(ControlDestination.Profile) },
            )
        },
    ) {
        when (destination) {
            ControlDestination.Sessions -> SessionsScreen(onOpen = ::openSession, onMenu = openMenu)
            ControlDestination.Session -> {
                val sessionId = selectedSessionId
                if (sessionId == null) {
                    SessionsScreen(onOpen = ::openSession, onMenu = openMenu)
                } else {
                    SessionScreen(
                        sessionId = sessionId,
                        initialSkillId = pendingSkillId,
                        onInitialSkillConsumed = { pendingSkillId = null },
                        onMenu = openMenu,
                        onBack = { navigate(ControlDestination.Sessions) },
                        onOpenSession = ::openSession,
                    )
                }
            }
            ControlDestination.Machines -> DevicesScreen(onMenu = openMenu)
            ControlDestination.ExternalSessions -> ExternalSessionsScreen(
                onMenu = openMenu,
                onOpenImported = ::openSession,
            )
            ControlDestination.Approvals -> ApprovalCenterScreen(onMenu = openMenu)
            ControlDestination.Extensions -> ExtensionsScreen(
                onMenu = openMenu,
                canUseSkill = selectedSessionId != null,
                onUseSkill = { skillId ->
                    if (selectedSessionId != null) {
                        pendingSkillId = skillId
                        navigate(ControlDestination.Session)
                    }
                },
            )
            ControlDestination.Artifacts -> ArtifactsScreen(selectedSessionId, onMenu = openMenu)
            ControlDestination.Settings -> SettingsScreen(
                onMenu = openMenu,
                onMachines = { navigate(ControlDestination.Machines) },
            )
            ControlDestination.Profile -> ProfileScreen(onMenu = openMenu, onOpenSession = ::openSession)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PairingScreen(onDone: () -> Unit = {}, onCancel: (() -> Unit)? = null, scanImmediately: Boolean = false) {
    var connectionLabel by rememberSaveable { mutableStateOf("Десктоп") }
    var url by rememberSaveable { mutableStateOf("192.168.0.155:47821") }
    var code by rememberSaveable { mutableStateOf("") }
    var name by rememberSaveable { mutableStateOf(android.os.Build.MODEL ?: "android") }
    var fingerprint by rememberSaveable { mutableStateOf("") }
    var advanced by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val savedConnections by Bridge.savedConnections.collectAsState()
    var scanned by remember { mutableStateOf<PairingQr?>(null) }
    val invalidQrText=uiText("Это не действующий QR-код сопряжения Игорька.", "This is not a valid Igorek pairing QR code.")
    val scanPrompt=uiText("Наведите камеру на QR-код в настройках десктопного Игорька", "Scan the QR code in Igorek Desktop settings")
    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        result.contents?.let { raw ->
            scanned = decodePairingQr(raw)
            if (scanned == null) error = invalidQrText
        }
    }
    fun scan() = scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
        .setCaptureActivity(QrCaptureActivity::class.java).setOrientationLocked(false)
        .setBeepEnabled(false).setBarcodeImageEnabled(false)
        .setPrompt(scanPrompt))
    LaunchedEffect(scanImmediately) { if (scanImmediately) scan() }
    scanned?.let { pairing ->
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { if (!busy) scanned = null },
            title = { Text(uiText("Подключиться к десктопу?", "Connect to desktop?")) },
            text = { Text(pairing.name + "\n" + pairing.url + "\n\n" + uiText("HTTPS-сертификат будет проверен по отпечатку из QR. Прежнее подключение сохранится.", "The HTTPS certificate will be pinned to this QR. Your previous connection will remain saved.")) },
            confirmButton = { TextButton(enabled = !busy, onClick = {
                busy = true; error = null
                Bridge.pair(pairing.url, pairing.code, name, pairing.fingerprint, pairing.name) { failure ->
                    busy = false; error = failure?.message; scanned = null
                    if (failure == null) onDone()
                }
            }) { Text(if (busy) uiText("Подключаю…", "Connecting…") else uiText("Подключить", "Connect")) } },
            dismissButton = { TextButton(enabled = !busy, onClick = { scanned = null }) { Text(uiText("Отмена", "Cancel")) } },
        )
    }

    Scaffold(topBar = { TopAppBar(title = { Text(uiText("Подключение к Игорьку", "Connect to Igorek")) }, actions = { NotificationCenterButton(); if (onCancel != null) TextButton(onClick = onCancel) { Text(uiText("Закрыть", "Close")) } }) }) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            savedConnections.filterNot { it.active }.forEach { saved ->
                OutlinedButton(enabled = !busy, onClick = {
                    busy = true
                    Bridge.switchConnection(saved.id) { failure -> busy = false; error = failure?.message; if (failure == null) onDone() }
                }, modifier = Modifier.fillMaxWidth()) { Text(uiText("Вернуться: ", "Return to: ") + saved.label) }
            }
            Button(onClick = { scan() }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text(uiText("Сканировать QR-код", "Scan QR code")) }
            Text(uiText("Или введите данные вручную", "Or enter the details manually"), style = MaterialTheme.typography.labelMedium)
            Text(
                uiText("1. В десктопе: Настройки → Мобильное приложение → Подключить телефон. Для Web UI: igorek-factory mobile pair", "1. Desktop: Settings → Mobile app → Connect phone. For Web UI: igorek-factory mobile pair"),
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                uiText("2. Введите адрес этого компьютера и полученный код.", "2. Enter that computer's address and the pairing code."),
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedTextField(value = connectionLabel, onValueChange = { connectionLabel = it }, label = { Text(uiText("Название подключения", "Connection name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                label = { Text(uiText("Адрес (host:port)", "Address (host:port)")) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = code,
                onValueChange = { code = it.uppercase() },
                label = { Text(uiText("Код сопряжения", "Pairing code")) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text(uiText("Название устройства", "Device name")) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedButton(onClick = { advanced = !advanced }) {
                Text(if (advanced) uiText("Скрыть дополнительные", "Hide advanced") else uiText("Дополнительно (TLS)", "Advanced (TLS)"))
            }
            if (advanced) {
                OutlinedTextField(
                    value = fingerprint,
                    onValueChange = { fingerprint = it },
                    label = { Text(uiText("Отпечаток сертификата SHA-256", "SHA-256 certificate fingerprint")) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            Button(
                onClick = {
                    busy = true
                    error = null
                    Bridge.pair(url, code, name, fingerprint, connectionLabel) { throwable ->
                        busy = false
                        error = throwable?.message
                        if (throwable == null) onDone()
                    }
                },
                enabled = !busy && url.isNotBlank() && code.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (busy) CircularProgressIndicator(Modifier.padding(end = 8.dp))
                Text(uiText("Подключить", "Connect"))
            }
            ErrorNotificationEffect(error, "pairing")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionsScreen(onOpen: (String) -> Unit, onMenu: () -> Unit) {
    val sessions by Bridge.sessions.collectAsState()
    val archiveCandidateCount by Bridge.archiveCandidateCount.collectAsState()
    val approvals by Bridge.approvals.collectAsState()
    val connection by Bridge.connection.collectAsState()
    var creating by rememberSaveable { mutableStateOf(false) }
    var createBusy by remember { mutableStateOf(false) }
    var profile by rememberSaveable { mutableStateOf("") }
    var cwd by rememberSaveable { mutableStateOf("") }
    var createKey by rememberSaveable { mutableStateOf("") }
    var createError by remember { mutableStateOf<String?>(null) }
    var createOptions by remember { mutableStateOf(SessionOptionsResponse()) }
    var createOptionsLoading by remember { mutableStateOf(false) }
    var showArchived by rememberSaveable { mutableStateOf(false) }
    var renameTarget by remember { mutableStateOf<SessionSummary?>(null) }
    var archiveAllConfirm by remember { mutableStateOf(false) }
    var archiveAllBusy by remember { mutableStateOf(false) }
    var archiveAllError by remember { mutableStateOf<String?>(null) }
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    renameTarget?.let { target ->
        RenameSessionDialog(
            initial = target.title,
            onDismiss = { renameTarget = null },
            onConfirm = { newTitle ->
                Bridge.renameSession(target.sessionId, newTitle) { renameTarget = null }
            },
        )
    }

    if (archiveAllConfirm) {
        AlertDialog(
            onDismissRequest = { if (!archiveAllBusy) archiveAllConfirm = false },
            title = { Text(uiText("Архивировать все чаты?", "Archive all chats?")) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        uiText(
                            "В архив будут перемещены ${chatCountLabel(archiveCandidateCount)}. Чаты с выполняющимися задачами останутся в списке.",
                            "${chatCountLabel(archiveCandidateCount)} will be archived. Chats with running tasks will remain in the list.",
                        )
                    )
                    archiveAllError?.let { error ->
                        ErrorNotificationEffect(error, "App")
                    }
                }
            },
            confirmButton = {
                TextButton(
                    enabled = !archiveAllBusy && archiveCandidateCount > 0,
                    onClick = {
                        archiveAllBusy = true
                        archiveAllError = null
                        Bridge.archiveAllSessions { result, error ->
                            archiveAllBusy = false
                            if (error != null) {
                                archiveAllError = error.message
                            } else if (result != null) {
                                archiveAllConfirm = false
                                scope.launch { snackbarHostState.showSnackbar(archiveResultText(result)) }
                            }
                        }
                    },
                ) { Text(if (archiveAllBusy) uiText("Архивируем…", "Archiving…") else uiText("Архивировать", "Archive")) }
            },
            dismissButton = {
                TextButton(
                    enabled = !archiveAllBusy,
                    onClick = { archiveAllConfirm = false },
                ) { Text(uiText("Отмена", "Cancel")) }
            },
        )
    }

    if (creating) {
        AlertDialog(
            onDismissRequest = { if (!createBusy) creating = false },
            title = { Text(uiText("Новый чат", "New chat")) },
            text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(uiText("Выберите сохранённые значения или введите новые вручную.", "Choose saved values or enter new ones manually."))
                if (createOptionsLoading) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        CircularProgressIndicator(Modifier.size(20.dp))
                        Text(uiText("Загружаем профили и папки…", "Loading profiles and folders…"))
                    }
                }
                SessionOptionField(
                    label = uiText("Профиль", "Profile"),
                    value = profile,
                    options = createOptions.selectableProfiles,
                    enabled = !createBusy && !createOptionsLoading,
                    onValueChange = { profile = it },
                )
                createOptions.profileCatalog.firstOrNull { it.name == profile }?.let { choice ->
                    Text(
                        (if (choice.local) uiText("Локально", "Local") else uiText("Облако", "Cloud")) + " · " + modelDisplayName(choice.model),
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                SessionOptionField(
                    label = uiText("Рабочая папка на компьютере", "Working folder on the computer"),
                    value = cwd,
                    options = createOptions.workdirs,
                    enabled = !createBusy && !createOptionsLoading,
                    onValueChange = { cwd = it },
                )
                createError?.let { ErrorNotificationEffect(it, "App") }
            } },
            confirmButton = { TextButton(
                enabled = !createBusy && !createOptionsLoading && profile.isNotBlank() && cwd.isNotBlank(),
                onClick = {
                createBusy = true
                createError = null
                Bridge.createSession(profile, cwd, createKey) { id, error ->
                    createBusy = false
                    createError = error?.message
                    if (id != null) {
                        creating = false
                        onOpen(id)
                    }
                }
            }) { Text(if (createBusy) uiText("Создаём…", "Creating…") else uiText("Создать", "Create")) } },
            dismissButton = { TextButton(enabled = !createBusy, onClick = { creating = false }) { Text(uiText("Отмена", "Cancel")) } },
        )
    }

    LaunchedEffect(Unit) {
        Bridge.refreshSessions()
        Bridge.refreshApprovals()
        while (true) {
            kotlinx.coroutines.delay(5_000)
            Bridge.refreshSessions()
            Bridge.refreshApprovals()
        }
    }

    LaunchedEffect(showArchived) {
        Bridge.setArchivedFilter(showArchived)
        Bridge.refreshSessions()
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            ControlTopBar(
                title = if (showArchived) uiText("Архив", "Archive") else uiText("Сессии", "Sessions"),
                subtitle = if (connection == "online") uiText("На связи", "Online") else uiText("Восстанавливаем связь…", "Reconnecting…"),
                connection = connection,
                onMenu = onMenu,
                actions = {
                    IconButton(onClick = {
                        profile = ""
                        cwd = ""
                        createOptions = SessionOptionsResponse()
                        createOptionsLoading = true
                        createKey = java.util.UUID.randomUUID().toString()
                        createError = null
                        creating = true
                        Bridge.loadSessionOptions { options, error ->
                            createOptionsLoading = false
                            if (options != null) {
                                createOptions = options
                                profile = options.defaultProfile
                                cwd = options.defaultCwd
                            } else {
                                profile = sessions.firstOrNull()?.profile.orEmpty()
                                cwd = sessions.firstOrNull()?.cwd.orEmpty()
                                createError = error?.message
                            }
                        }
                    }) { Icon(Icons.Default.Add, contentDescription = uiText("Новый чат", "New chat")) }
                    IconButton(onClick = { Bridge.refreshSessions(); Bridge.refreshApprovals() }) {
                        Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить", "Refresh"))
                    }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item(key = "archive-filter") {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(if (showArchived) uiText("Архив", "Archive") else uiText("Чаты", "Chats"), style = MaterialTheme.typography.titleSmall)
                    if (!showArchived) {
                        OutlinedButton(
                            enabled = archiveCandidateCount > 0 && !archiveAllBusy,
                            onClick = {
                                archiveAllError = null
                                archiveAllConfirm = true
                            },
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 0.dp),
                        ) {
                            Icon(Icons.Default.Archive, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(uiText("Архивировать все", "Archive all"))
                        }
                    }
                    TextButton(onClick = { showArchived = !showArchived }) {
                        Text(if (showArchived) uiText("К активным", "Active chats") else uiText("Архив", "Archive"))
                    }
                }
            }
            if (approvals.isNotEmpty()) {
                item {
                    Text(
                        uiText("Ожидают подтверждения: ${approvals.size}", "Pending approvals: ${approvals.size}"),
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.titleSmall,
                    )
                }
            }
            items(sessions, key = { it.sessionId }) { session ->
                SessionCard(
                    session = session,
                    onOpen = { onOpen(session.sessionId) },
                    onRename = { renameTarget = session },
                    onArchive = { Bridge.archiveSession(session.sessionId, !session.archived) {} },
                )
            }
            if (sessions.isEmpty()) {
                item {
                    Text(
                        if (showArchived) uiText("Архив пуст.", "Archive is empty.") else uiText("Сессий пока нет.", "No sessions yet."),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
            item(key = "rename-hint") {
                Text(
                    uiText("Долгое нажатие на чат — переименовать или убрать в архив.", "Long-press a chat to rename or archive it."),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
internal fun ServerAddressDialog(current: String, onDismiss: () -> Unit) {
    var address by rememberSaveable { mutableStateOf(current) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val connectionFailure = uiText(
        "Не удалось подключиться к ${Bridge.normalizeUrl(address)}. Проверьте адрес, проброс порта и VPN.",
        "Could not connect to ${Bridge.normalizeUrl(address)}. Check the address, port forwarding, and VPN.",
    )
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(uiText("Адрес компьютера с Игорьком", "Igorek computer address")) },
        text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(uiText(
                "Для мобильной сети нужен внешний адрес дома с пробросом порта. Адрес VPN-сервера сюда вводить не нужно.",
                "Mobile access needs your home's external address with port forwarding. Do not enter the VPN server address here.",
            ))
            OutlinedTextField(
                address,
                { address = it },
                label = { Text(uiText("http(s)://адрес:порт", "http(s)://address:port")) },
                enabled = !busy,
                singleLine = true,
            )
            Text(
                uiText("Проверим подключение, сохранив сопряжение и сессии.", "The connection will be checked without losing pairing or sessions."),
                style = MaterialTheme.typography.bodySmall,
            )
            error?.let { ErrorNotificationEffect(it, "App") }
        } },
        confirmButton = { TextButton(enabled = !busy && address.isNotBlank(), onClick = {
            busy = true
            error = null
            Bridge.changeServer(address) { failure ->
                busy = false
                if (failure == null) onDismiss()
                else error = connectionFailure
            }
        }) { Text(if (busy) uiText("Проверяем…", "Checking…") else uiText("Подключиться", "Connect")) } },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !busy) { Text(uiText("Отмена", "Cancel")) } },
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun SessionCard(
    session: SessionSummary,
    onOpen: () -> Unit,
    onRename: () -> Unit,
    onArchive: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    Box {
        Card(
            modifier = Modifier.fillMaxWidth().combinedClickable(
                onClick = onOpen,
                onLongClick = { menu = true },
            ),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        ) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(session.title.ifBlank { session.sessionId }, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (session.mcpPreview.isNotBlank()) {
                    Text(
                        session.mcpPreview,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (session.archived) {
                        Text(
                            uiText("архив", "archived"),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.secondary,
                        )
                    }
                    Text(
                        sessionStatusLabel(session, LocalAppLanguage.current),
                        style = MaterialTheme.typography.labelSmall,
                        color = when {
                            session.activeStatus == "cancelling" -> MaterialTheme.colorScheme.tertiary
                            session.active -> MaterialTheme.colorScheme.primary
                            else -> MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                    Text(session.profile, style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace)
                    if (session.pendingApprovals > 0) {
                        Text(
                            uiText("подтверждения: ${session.pendingApprovals}", "approvals: ${session.pendingApprovals}"),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(
                text = { Text(uiText("Переименовать", "Rename")) },
                leadingIcon = { Icon(Icons.Default.Edit, contentDescription = null) },
                onClick = {
                    menu = false
                    onRename()
                },
            )
            DropdownMenuItem(
                text = {
                    Text(if (session.archived) uiText("Убрать из архива", "Restore from archive") else uiText("В архив", "Archive"))
                },
                leadingIcon = { Icon(Icons.Default.Archive, contentDescription = null) },
                onClick = {
                    menu = false
                    onArchive()
                },
            )
        }
    }
}

@Composable
private fun RenameSessionDialog(
    initial: String,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    var value by rememberSaveable(initial) { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(uiText("Переименовать чат", "Rename chat")) },
        text = {
            OutlinedTextField(
                value = value,
                onValueChange = { value = it },
                label = { Text(uiText("Название", "Name")) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = { TextButton(onClick = { onConfirm(value.trim()) }) { Text(uiText("Сохранить", "Save")) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(uiText("Отмена", "Cancel")) } },
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicesScreen(onMenu: () -> Unit) {
    val devices by Bridge.devices.collectAsState()
    val currentDeviceId by Bridge.currentDeviceId.collectAsState()
    var confirmPurge by remember { mutableStateOf(false) }
    var pendingDelete by remember { mutableStateOf<DeviceDto?>(null) }
    val revokedCount = devices.count { it.revokedAt != null }

    LaunchedEffect(Unit) { Bridge.loadDevices() }

    Scaffold(
        topBar = {
            ControlTopBar(
                title = uiText("Машины", "Devices"),
                subtitle = uiText("Устройства с доступом к Игорьку", "Devices with access to Igorek"),
                onMenu = onMenu,
                actions = {
                    if (revokedCount > 0) {
                        IconButton(onClick = { confirmPurge = true }) {
                            Icon(
                                Icons.Default.DeleteSweep,
                                contentDescription = uiText("Удалить отозванные", "Delete revoked"),
                            )
                        }
                    }
                    IconButton(onClick = { Bridge.loadDevices() }) {
                        Icon(Icons.Default.Refresh, contentDescription = uiText("Обновить", "Refresh"))
                    }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(devices, key = { it.deviceId }) { device ->
                DeviceCard(
                    device = device,
                    isCurrent = device.deviceId == currentDeviceId,
                    onRevoke = { Bridge.revokeDevice(device.deviceId) },
                    onDelete = { pendingDelete = device },
                )
            }
        }
    }

    if (confirmPurge) {
        AlertDialog(
            onDismissRequest = { confirmPurge = false },
            title = { Text(uiText("Удалить отозванные устройства?", "Delete revoked devices?")) },
            text = {
                Text(
                    uiText(
                        "Из списка машин пропадут $revokedCount отозванных записей. Текущее устройство останется.",
                        "$revokedCount revoked entries will be removed from the device list. The current device stays.",
                    ),
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmPurge = false
                        Bridge.purgeRevokedDevices()
                    },
                ) { Text(uiText("Удалить", "Delete")) }
            },
            dismissButton = {
                TextButton(onClick = { confirmPurge = false }) { Text(uiText("Отмена", "Cancel")) }
            },
        )
    }

    val deleteTarget = pendingDelete
    if (deleteTarget != null) {
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text(uiText("Удалить устройство?", "Delete device?")) },
            text = {
                Text(
                    uiText(
                        "Запись «${deleteTarget.name.ifBlank { deleteTarget.deviceId }}» исчезнет из списка машин.",
                        "Entry \"${deleteTarget.name.ifBlank { deleteTarget.deviceId }}\" will disappear from the device list.",
                    ),
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        pendingDelete = null
                        Bridge.deleteDevice(deleteTarget.deviceId)
                    },
                ) { Text(uiText("Удалить", "Delete")) }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }) { Text(uiText("Отмена", "Cancel")) }
            },
        )
    }
}

@Composable
private fun DeviceCard(
    device: DeviceDto,
    isCurrent: Boolean,
    onRevoke: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(device.name, style = MaterialTheme.typography.bodyMedium)
                Text(device.deviceId, style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace)
                if (isCurrent) {
                    Text(
                        uiText("это устройство", "this device"),
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                if (device.revokedAt != null) {
                    Text(uiText("доступ отозван", "access revoked"), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelSmall)
                }
            }
            when {
                device.revokedAt != null -> IconButton(onClick = onDelete) {
                    Icon(Icons.Default.DeleteForever, contentDescription = uiText("Удалить", "Delete"))
                }
                !isCurrent -> IconButton(onClick = onRevoke) {
                    Icon(Icons.Default.Delete, contentDescription = uiText("Отозвать", "Revoke"))
                }
            }
        }
    }
}
