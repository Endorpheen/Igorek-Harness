package ru.igorek.remote.ui

import android.app.TimePickerDialog
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import ru.igorek.remote.data.*

private fun scheduleError(error: Throwable, language: AppLanguage): String {
    fun text(ru: String, en: String) = if (language == AppLanguage.Russian) ru else en
    return when {
        error is BridgeException && error.code == "connection_changed" -> text("Подключение изменилось. Открой профиль заново.", "Connection changed. Reopen your profile.")
        error is BridgeException && error.status == 404 -> text("Расписание или сервер недоступны. Обнови список; для старой версии обнови десктоп.", "The schedule or server is unavailable. Refresh the list; update older desktop versions.")
        error is BridgeException && error.status in setOf(401, 403) -> text("Нет доступа к расписанию этого подключения.", "No access to this connection's schedules.")
        error is BridgeException && error.status in setOf(400, 409) -> error.message.orEmpty()
        else -> text("Не удалось получить ответ. Проверь соединение и обнови список перед повтором действия.", "No response. Check the connection and refresh the list before repeating an action.")
    }
}

@Composable
internal fun SchedulesPanel(modifier: Modifier = Modifier, profileHeader: @Composable () -> Unit, onOpenSession: (String) -> Unit) {
    val server by Bridge.serverUrl.collectAsState()
    val device by Bridge.currentDeviceId.collectAsState()
    val paired by Bridge.paired.collectAsState()
    val saved by Bridge.savedConnections.collectAsState()
    val label = saved.firstOrNull { it.active }?.label.orEmpty().ifBlank { server }
    key(server, device, paired) {
        val connection = remember { if (paired) runCatching { Bridge.schedulesConnection() }.getOrNull() else null }
        ScheduleContent(connection, label, modifier, profileHeader, onOpenSession)
    }
}

private data class ScheduleEditorTarget(val initial: ScheduledJobDto? = null)

@Composable
private fun ScheduleContent(connection: SchedulesConnection?, connectionLabel: String, modifier: Modifier, profileHeader: @Composable () -> Unit, onOpenSession: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val language = LocalAppLanguage.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val gate = remember { Mutex() }
    var snapshot by remember { mutableStateOf<SchedulesResponse?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<ScheduleEditorTarget?>(null) }
    var deleting by remember { mutableStateOf<ScheduledJobDto?>(null) }
    var details by remember { mutableStateOf<ScheduledJobDto?>(null) }
    suspend fun reload() {
        if (connection == null || busy || loading) return
        loading = true
        try { snapshot = gate.withLock { connection.list() }; error = null }
        catch (cancel: CancellationException) { throw cancel }
        catch (failure: Exception) { error = scheduleError(failure, language) }
        finally { loading = false }
    }
    fun mutate(action: suspend (SchedulesConnection) -> SchedulesResponse) {
        if (connection == null || busy || error != null) return
        busy = true
        scope.launch {
            try { snapshot = gate.withLock { action(connection) }; error = null; Bridge.refreshSessions() }
            catch (cancel: CancellationException) { throw cancel }
            catch (failure: Exception) { error = scheduleError(failure, language) }
            finally { busy = false }
        }
    }
    LaunchedEffect(connection, lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (isActive) {
                if (editing == null && !busy) reload()
                delay(10000)
            }
        }
    }
    val available = connection != null && snapshot?.available == true
    val actionsEnabled = available && error == null && !busy
    LazyColumn(modifier, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { profileHeader() }
        item { HorizontalDivider(Modifier.padding(vertical = 4.dp)) }
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Schedule, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(uiText("Расписание", "Scheduled tasks"), style = MaterialTheme.typography.titleMedium)
                    Text(connectionLabel, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (loading) Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp) }
                else IconButton(onClick = { scope.launch { reload() } }, enabled = connection != null && !busy) { Icon(Icons.Default.Refresh, uiText("Обновить расписания", "Refresh schedules")) }
                IconButton(onClick = { if (actionsEnabled) editing = ScheduleEditorTarget() }, enabled = actionsEnabled) { Icon(Icons.Default.Add, uiText("Создать задание", "Create scheduled task"), tint = if (actionsEnabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        }
        if (connection == null) item { Text(uiText("Подключись к Игорьку, чтобы увидеть задания.", "Connect to Igorek to view scheduled tasks."), style = MaterialTheme.typography.bodyMedium) }
        error?.let { message -> item { Text(message, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) } }
        if (snapshot?.available == false) item { Text(uiText("Расписания доступны при подключении к запущенному десктопному Игорьку.", "Schedules are available when connected to running desktop Igorek."), style = MaterialTheme.typography.bodyMedium) }
        if (available && snapshot?.running == false) item { Text(uiText("Планировщик остановлен. Автоматические запуски сейчас не выполняются.", "The scheduler is stopped. Automatic runs are not active."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
        if (available && snapshot?.schedules.isNullOrEmpty()) item {
            Column(Modifier.fillMaxWidth().padding(vertical = 20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(uiText("Пока нет заданий", "No scheduled tasks yet"), style = MaterialTheme.typography.titleSmall)
                Text(uiText("Нажми +, чтобы добавить первое.", "Tap + to add your first task."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        items(snapshot?.schedules.orEmpty(), key = { it.scheduleId }) { row ->
            ScheduleCard(row, actionsEnabled, onDetails = { details = row }, onEdit = { editing = ScheduleEditorTarget(row) },
                onToggle = { mutate { it.enabled(row.scheduleId, !row.enabled) } }, onRun = { mutate { it.run(row.scheduleId) } },
                onDelete = { deleting = row }, onResult = { row.lastSessionId?.takeIf(String::isNotBlank)?.let(onOpenSession) })
        }
        if (busy) item { LinearProgressIndicator(Modifier.fillMaxWidth()) }
    }
    editing?.let { target ->
        if (connection != null) key(target.initial?.scheduleId ?: "new") {
            ScheduleEditor(connection, gate, target.initial, onDismiss = { editing = null }, onSaved = { snapshot = it; error = null; editing = null })
        }
    }
    deleting?.let { row ->
        AlertDialog(onDismissRequest = { deleting = null }, title = { Text(uiText("Удалить задание?", "Delete scheduled task?")) },
            text = { Text(row.name + "\n\n" + uiText("Будущие запуски будут удалены. Чаты прежних запусков сохранятся.", "Future runs will be removed. Previous run chats will remain.")) },
            confirmButton = { TextButton(onClick = { deleting = null; mutate { it.delete(row.scheduleId) } }) { Text(uiText("Удалить", "Delete"), color = MaterialTheme.colorScheme.error) } },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text(uiText("Отмена", "Cancel")) } })
    }
    details?.let { row -> ScheduleDetails(row, onDismiss = { details = null }, onResult = { details = null; row.lastSessionId?.takeIf(String::isNotBlank)?.let(onOpenSession) }) }
}

@Composable
private fun ScheduleCard(row: ScheduledJobDto, enabled: Boolean, onDetails: () -> Unit, onEdit: () -> Unit, onToggle: () -> Unit, onRun: () -> Unit, onDelete: () -> Unit, onResult: () -> Unit) {
    val language = LocalAppLanguage.current
    val status = scheduleStatus(row)
    var menu by remember(row.scheduleId) { mutableStateOf(false) }
    LaunchedEffect(enabled) { if (!enabled) menu = false }
    OutlinedCard(shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.clickable(onClick = onDetails).padding(start = 14.dp, end = 6.dp, top = 6.dp, bottom = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(row.name, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(if (language == AppLanguage.Russian) status.ru else status.en, style = MaterialTheme.typography.labelSmall,
                        color = if (status in setOf(ScheduleStatus.Approval, ScheduleStatus.Failed)) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
                }
                Box {
                    IconButton(onClick = { if (enabled) menu = true }, enabled = enabled) { Icon(Icons.Default.MoreHoriz, uiText("Действия: ", "Actions: ") + row.name) }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(text = { Text(uiText("Выполнить сейчас", "Run now")) }, leadingIcon = { Icon(Icons.Default.PlayArrow, null) }, enabled = row.ready, onClick = { menu = false; onRun() })
                        DropdownMenuItem(text = { Text(if (row.scheduleType in setOf("daily", "once")) uiText("Изменить", "Edit") else uiText("Редактирование Cron недоступно", "Cron editing is unavailable")) }, leadingIcon = { Icon(Icons.Default.Edit, null) }, enabled = row.scheduleType in setOf("daily", "once"), onClick = { menu = false; onEdit() })
                        DropdownMenuItem(text = { Text(if (row.enabled) uiText("Приостановить", "Pause schedule") else uiText("Возобновить", "Resume schedule")) }, leadingIcon = { Icon(if (row.enabled) Icons.Default.Pause else Icons.Default.PlayArrow, null) }, onClick = { menu = false; onToggle() })
                        if (!row.lastSessionId.isNullOrBlank()) DropdownMenuItem(text = { Text(uiText("Открыть последний результат", "Open last result")) }, leadingIcon = { Icon(Icons.Default.OpenInNew, null) }, onClick = { menu = false; onResult() })
                        HorizontalDivider()
                        DropdownMenuItem(text = { Text(uiText("Удалить", "Delete"), color = MaterialTheme.colorScheme.error) }, leadingIcon = { Icon(Icons.Default.DeleteOutline, null) }, onClick = { menu = false; onDelete() })
                    }
                }
            }
            if (!row.enabled && status != ScheduleStatus.Paused) Text(uiText("Расписание на паузе", "Schedule paused"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(row.payload, modifier = Modifier.padding(end = 8.dp), style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            Text(scheduleRepeat(row) + " · " + row.timezone, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(uiText("Следующий: ", "Next: ") + scheduleTimestamp(row.nextRunAt, row.timezone, language), style = MaterialTheme.typography.labelSmall)
            if (row.lastStatus.isNotBlank()) Text(uiText("Последний: ", "Last: ") + scheduleLastStatus(row.lastStatus), style = MaterialTheme.typography.labelSmall, color = if (row.lastStatus in setOf("fail", "timeout", "dead_letter")) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun scheduleRepeat(row: ScheduledJobDto): String = when (row.scheduleType) {
    "daily" -> uiText("Каждый день", "Daily") + " · " + row.timeValue.orEmpty()
    "once" -> uiText("Один раз", "Once") + " · " + row.timeValue.orEmpty()
    else -> "Cron · " + row.cronExpression.orEmpty()
}

@Composable
private fun scheduleLastStatus(status: String): String = when (status) {
    "ok" -> uiText("выполнено", "completed")
    "fail", "dead_letter" -> uiText("ошибка", "failed")
    "timeout" -> uiText("истекло время", "timed out")
    "cancelled" -> uiText("отменено", "cancelled")
    "queued" -> uiText("в очереди", "queued")
    "processing" -> uiText("в работе", "running")
    "waiting_approval" -> uiText("нужно подтверждение", "approval needed")
    else -> status
}

@Composable
private fun ScheduleDetails(row: ScheduledJobDto, onDismiss: () -> Unit, onResult: () -> Unit) {
    val language = LocalAppLanguage.current
    AlertDialog(onDismissRequest = onDismiss, title = { Text(row.name) }, text = {
        SelectionContainer { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(row.payload)
            Text(scheduleRepeat(row) + " · " + row.timezone, style = MaterialTheme.typography.bodySmall)
            Text(uiText("Следующий: ", "Next: ") + scheduleTimestamp(row.nextRunAt, row.timezone, language), style = MaterialTheme.typography.bodySmall)
            Text(uiText("Последний запуск: ", "Last run: ") + scheduleTimestamp(row.lastRunAt, row.timezone, language), style = MaterialTheme.typography.bodySmall)
            Text(uiText("Запусков: ", "Runs: ") + row.runCount + " · " + if (row.enabled) uiText("автозапуск включён", "automatic runs enabled") else uiText("автозапуск на паузе", "automatic runs paused"), style = MaterialTheme.typography.bodySmall)
            Text(uiText("Профиль: ", "Profile: ") + row.profile + "\n" + uiText("Папка: ", "Folder: ") + row.cwd, style = MaterialTheme.typography.bodySmall)
            if (row.scheduleType == "cron") Text(uiText("Этот редактор поддерживает ежедневные и разовые задания.", "This editor supports daily and one-time tasks."), style = MaterialTheme.typography.bodySmall)
        } }
    }, confirmButton = { TextButton(onClick = onDismiss) { Text(uiText("Закрыть", "Close")) } },
        dismissButton = { if (!row.lastSessionId.isNullOrBlank()) TextButton(onClick = onResult) { Text(uiText("Результат", "Result")) } })
}

@Composable
private fun ScheduleEditor(connection: SchedulesConnection, gate: Mutex, initial: ScheduledJobDto?, onDismiss: () -> Unit, onSaved: (SchedulesResponse) -> Unit) {
    val context = LocalContext.current
    val focus = LocalFocusManager.current
    val scope = rememberCoroutineScope()
    val language = LocalAppLanguage.current
    var name by remember { mutableStateOf(initial?.name.orEmpty()) }
    var payload by remember { mutableStateOf(initial?.payload.orEmpty()) }
    var profile by remember { mutableStateOf(initial?.profile.orEmpty()) }
    var cwd by remember { mutableStateOf(initial?.cwd.orEmpty()) }
    var timezone by remember { mutableStateOf(initial?.timezone ?: "Europe/Moscow") }
    var time by remember { mutableStateOf(initial?.timeValue ?: "09:00") }
    var repeat by remember { mutableStateOf(initial?.scheduleType ?: "daily") }
    var options by remember { mutableStateOf<SessionOptionsResponse?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var advanced by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var optionsRefresh by remember { mutableIntStateOf(0) }
    var profileMenu by remember { mutableStateOf(false) }
    var folderMenu by remember { mutableStateOf(false) }
    val invalidMessage = uiText("Заполни название, задание, время и выбери работающий профиль. Папка и часовой пояс должны быть заполнены.", "Enter a name, task, time and active profile. Folder and time zone must be filled in.")
    LaunchedEffect(connection, optionsRefresh) {
        loading = true
        try {
            options = gate.withLock { connection.options() }
            if (profile.isBlank()) profile = options!!.defaultProfile.ifBlank { options!!.profiles.firstOrNull().orEmpty() }
            if (cwd.isBlank()) cwd = options!!.defaultCwd.ifBlank { options!!.workdirs.firstOrNull().orEmpty() }
            error = null
        } catch (cancel: CancellationException) { throw cancel }
        catch (failure: Exception) { error = scheduleError(failure, language) }
        finally { loading = false }
    }
    Dialog(onDismissRequest = { if (!busy) onDismiss() }, properties = DialogProperties(usePlatformDefaultWidth = false, dismissOnBackPress = !busy, dismissOnClickOutside = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).imePadding()) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onDismiss, enabled = !busy) { Icon(Icons.Default.Close, uiText("Закрыть редактор", "Close editor")) }
                    Text(if (initial == null) uiText("Новое задание", "New scheduled task") else uiText("Изменить задание", "Edit scheduled task"), Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                    if (loading || busy) Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp) }
                    else IconButton(onClick = { optionsRefresh++ }) { Icon(Icons.Default.Refresh, uiText("Обновить профили", "Refresh profiles")) }
                    IconButton(enabled = !loading && !busy && !options?.profiles.isNullOrEmpty(), onClick = {
                        val fields = ScheduleFields(name.trim(), payload.trim(), profile, cwd.trim(), repeat, time, timezone.trim())
                        if (!scheduleFieldsValid(fields, options?.profiles.orEmpty())) { error = invalidMessage; if (cwd.isBlank() || timezone.isBlank()) advanced = true }
                        else {
                            busy = true; focus.clearFocus()
                            scope.launch {
                                try { onSaved(gate.withLock { connection.save(fields, initial?.scheduleId) }) }
                                catch (cancel: CancellationException) { throw cancel }
                                catch (failure: Exception) { error = scheduleError(failure, language) }
                                finally { busy = false }
                            }
                        }
                    }) { Icon(Icons.Default.Check, uiText("Сохранить задание", "Save scheduled task"), tint = MaterialTheme.colorScheme.primary) }
                }
                HorizontalDivider()
                error?.let { Text(it, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, maxLines = 4, overflow = TextOverflow.Ellipsis) }
                Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    OutlinedTextField(name, onValueChange = { name = it.take(160) }, label = { Text(uiText("Название", "Name")) }, modifier = Modifier.fillMaxWidth(), enabled = !busy, singleLine = true)
                    OutlinedTextField(payload, onValueChange = { payload = it.take(16000) }, label = { Text(uiText("Что сделать", "Task")) }, modifier = Modifier.fillMaxWidth(), enabled = !busy, minLines = 3, maxLines = 8)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(selected = repeat == "daily", onClick = { repeat = "daily" }, label = { Text(uiText("Каждый день", "Daily")) }, enabled = !busy, modifier = Modifier.weight(1f))
                        FilterChip(selected = repeat == "once", onClick = { repeat = "once" }, label = { Text(uiText("Один раз сегодня", "Once today")) }, enabled = !busy, modifier = Modifier.weight(1f))
                    }
                    OutlinedButton(enabled = !busy, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp), onClick = {
                        focus.clearFocus()
                        val parts = time.split(':')
                        TimePickerDialog(context, { _, hour, minute -> time = "%02d:%02d".format(java.util.Locale.ROOT, hour, minute) }, parts.getOrNull(0)?.toIntOrNull() ?: 9, parts.getOrNull(1)?.toIntOrNull() ?: 0, true).show()
                    }) { Icon(Icons.Outlined.Schedule, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text(time + " · " + timezone, maxLines = 2, overflow = TextOverflow.Ellipsis) }
                    Box {
                        OutlinedButton(enabled = !busy && !loading, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp), onClick = { profileMenu = true }) {
                            Text(uiText("Профиль: ", "Profile: ") + profile.ifBlank { "—" }, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis); Icon(Icons.Default.ArrowDropDown, null)
                        }
                        DropdownMenu(expanded = profileMenu, onDismissRequest = { profileMenu = false }) {
                            options?.profiles.orEmpty().forEach { value -> DropdownMenuItem(text = { Text(value) }, onClick = { profile = value; profileMenu = false }) }
                        }
                    }
                    if (!loading && (options?.profiles.isNullOrEmpty() || profile !in options!!.profiles)) Text(uiText("Запусти исполнитель нужного профиля на компьютере, затем обнови список.", "Start the profile's executor on your computer, then refresh the list."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    Row(Modifier.fillMaxWidth().clickable { advanced = !advanced }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Tune, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text(uiText("Папка и часовой пояс", "Folder and time zone"), Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium); Icon(if (advanced) Icons.Default.ExpandLess else Icons.Default.ExpandMore, null)
                    }
                    if (advanced) {
                        Box {
                            OutlinedTextField(cwd, onValueChange = { cwd = it.take(4096) }, label = { Text(uiText("Рабочая папка на компьютере", "Working folder on the computer")) }, enabled = !busy, singleLine = true, modifier = Modifier.fillMaxWidth(), trailingIcon = {
                                if (!options?.workdirs.isNullOrEmpty()) IconButton(onClick = { folderMenu = true }) { Icon(Icons.Default.ArrowDropDown, uiText("Выбрать папку", "Choose folder")) }
                            })
                            DropdownMenu(expanded = folderMenu, onDismissRequest = { folderMenu = false }) {
                                options?.workdirs.orEmpty().forEach { value -> DropdownMenuItem(text = { Text(value, maxLines = 2, overflow = TextOverflow.Ellipsis) }, onClick = { cwd = value; folderMenu = false }) }
                            }
                        }
                        OutlinedTextField(timezone, onValueChange = { timezone = it.take(80) }, label = { Text(uiText("Часовой пояс", "Time zone")) }, modifier = Modifier.fillMaxWidth(), enabled = !busy, singleLine = true)
                    }
                    Text(uiText("Автоматический запуск работает, пока десктоп и выбранный исполнитель запущены.", "Automatic runs require the desktop and the selected executor to be running."), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}
