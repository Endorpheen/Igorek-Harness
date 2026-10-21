package ru.igorek.remote.data

import android.content.Context
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withTimeoutOrNull
import android.net.ConnectivityManager
import android.net.Network
import android.os.SystemClock
import kotlinx.serialization.encodeToString
import kotlinx.serialization.decodeFromString
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.ConcurrentHashMap
import java.util.UUID
import java.io.OutputStream
import java.io.IOException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull
import okhttp3.Call

data class Alert(val title: String, val text: String, val sessionId: String?)

internal enum class BridgeErrorKind { CONNECTION, OPERATION }

internal fun bridgeErrorKind(error: Throwable): BridgeErrorKind =
    if (error is IOException && error !is BridgeException) BridgeErrorKind.CONNECTION
    else BridgeErrorKind.OPERATION

internal fun shouldClearBridgeErrorAfterReconnect(kind: BridgeErrorKind?): Boolean =
    kind == BridgeErrorKind.CONNECTION

internal const val SECURITY_LEVEL_DEFAULT = "standard"

// Security levels order matches backend (security_levels.LEVELS): higher rank means weaker security. Used for determining degradation relative to baseline.
internal val SECURITY_LEVEL_ORDER = listOf("strict", "standard", "trusted", "full")

internal fun normalizeSecurityLevel(raw: String?): String {
    val value = raw?.trim()?.lowercase().orEmpty()
    return if (value in SECURITY_LEVEL_ORDER) value else SECURITY_LEVEL_DEFAULT
}

internal fun securityLevelRank(level: String?): Int =
    SECURITY_LEVEL_ORDER.indexOf(normalizeSecurityLevel(level)).coerceAtLeast(0)

internal fun isSecurityLevelWeakened(level: String?, baseline: String?): Boolean =
    securityLevelRank(level) > securityLevelRank(baseline)

// The server requires confirmation to reduce the baseline level (not the current level).
// We duplicate this rule on the client to prevent a PATCH request for reducing the baseline without confirmation from being sent.
internal fun securityChangeNeedsConfirmation(
    targetLevel: String?,
    settings: SecuritySettingsResponse?,
): Boolean = isSecurityLevelWeakened(targetLevel, settings?.baselineLevel)

// Current effective level: baseline reduction without a future expires_at.
// The server rejects reading requests, so the UI must display the baseline, not the raw value.
internal fun securityEffectiveLevel(
    settings: SecuritySettingsResponse?,
    nowSeconds: Long = System.currentTimeMillis() / 1000,
): String {
    if (settings == null) return SECURITY_LEVEL_DEFAULT
    val level = normalizeSecurityLevel(settings.level)
    val baseline = normalizeSecurityLevel(settings.baselineLevel)
    if (!isSecurityLevelWeakened(level, baseline)) return level
    val expiresAt = settings.expiresAt
    return if (expiresAt != null && expiresAt > nowSeconds) level else baseline
}

internal fun securityBadgeVisible(
    settings: SecuritySettingsResponse?,
    nowSeconds: Long = System.currentTimeMillis() / 1000,
): Boolean {
    if (settings == null) return false
    val weakened = settings.isWeakened ||
        isSecurityLevelWeakened(settings.level, settings.baselineLevel)
    if (!weakened) return false
    val expiresAt = settings.expiresAt ?: return false
    return expiresAt > nowSeconds
}

internal fun securityLevelOptions(settings: SecuritySettingsResponse?): List<String> {
    val available = settings?.availableLevels.orEmpty()
        .map { it.trim().lowercase() }
        .filter { it in SECURITY_LEVEL_ORDER }
        .distinct()
    return available.ifEmpty { SECURITY_LEVEL_ORDER }
}

internal data class SecurityExpiry(val minutesLeft: Long, val expired: Boolean)

internal fun securityExpiry(expiresAt: Long?, nowSeconds: Long): SecurityExpiry? {
    if (expiresAt == null) return null
    val left = expiresAt - nowSeconds
    return if (left <= 0) SecurityExpiry(minutesLeft = 0, expired = true)
    else SecurityExpiry(minutesLeft = (left + 59) / 60, expired = false)
}

internal const val SANDBOX_REASON_ENV_DISABLED = "env_disabled"
internal const val SANDBOX_REASON_USER_DISABLED = "user_disabled"
internal const val SANDBOX_REASON_BWRAP_MISSING = "bwrap_missing"
internal const val SANDBOX_REASON_NAMESPACE_BLOCKED = "namespace_blocked"
internal const val SANDBOX_REASON_OBSERVE = "observe"

internal fun sandboxReasonCode(reason: String?): String = reason?.trim()?.lowercase().orEmpty()

// Disabling an already enabled sandbox results in a security weakening, requiring explicit confirmation from the server.
// Enabling is applied immediately.
internal fun sandboxChangeNeedsConfirmation(
    targetEnabled: Boolean,
    settings: SandboxSettingsResponse?,
): Boolean = !targetEnabled && (settings?.enabled ?: true)

// Sandbox can always be turned off; it can only be enabled if `bwrap` is available
// (otherwise, the server will respond with `bwrap_missing`/`namespace_blocked`, and the configuration will fail).
internal fun sandboxSwitchEnabled(settings: SandboxSettingsResponse?): Boolean {
    val value = settings ?: return false
    return value.enabled || value.available
}

object Bridge {

    private const val LONG_TASK_SECONDS = 180L

    private var prefs: Prefs? = null
    private val _screenshotsBlocked = MutableStateFlow(false)
    val screenshotsBlocked = _screenshotsBlocked.asStateFlow()

    fun setScreenshotsBlocked(blocked: Boolean) {
        prefs?.screenshotsBlocked = blocked
        _screenshotsBlocked.value = blocked
    }
    @Volatile private var client: BridgeClient? = null
    private var streamCall: Call? = null
    private var streamJob: Job? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val timelines = mutableMapOf<String, SessionTimeline>()
    private val timelineLock = Any()
    private val taskStartedAt = mutableMapOf<String, Long>()
    private val sessionsRefresh = Mutex()
    private val profileRouteGate = ProfileRouteGate()
    private val _profileSwitching = MutableStateFlow(false)
    val profileSwitching = _profileSwitching.asStateFlow()
    private val approvalsRefresh = Mutex()
    private val filesRefresh = Mutex()
    private val changesRefresh = Mutex()
    private val diffRefresh = Mutex()
    private val checkpointsRefresh = Mutex()
    private val checkpointChangesRefresh = Mutex()
    private val checkpointDiffRefresh = Mutex()
    private val errorLock = Any()
    private var lastErrorKind: BridgeErrorKind? = null
    private var lastDiagnostic: ErrorObservation? = null
    private val sessionGeneration = AtomicLong()
    @Volatile private var selectedSessionId: String? = null
    @Volatile private var pendingDiffPath: String? = null
    @Volatile private var selectedCheckpointHash: String? = null
    @Volatile private var pendingCheckpointDiffPath: String? = null
    @Volatile private var archivedOnly = false
    private val pendingMessages = ConcurrentHashMap<String, String>()
    private var replayThrough = 0L
    private val networkWake = Channel<Unit>(Channel.CONFLATED)
    @Volatile private var networkAvailable = true
    private var connectivityCallback: ConnectivityManager.NetworkCallback? = null
    private val diagnosticJson = kotlinx.serialization.json.Json { ignoreUnknownKeys=true; encodeDefaults=true }
    private var notificationWriteFailures = 0
    private var httpReadySince = 0L
    private var streamReadyJob: Job? = null
    @Volatile private var activityVisible = true
    private var resumedAt = -1L
    private var backgroundTimeoutJob: Job? = null

    fun setActivityVisible(visible: Boolean) {
        synchronized(errorLock) {
            if (activityVisible == visible) return
            activityVisible = visible
            backgroundTimeoutJob?.cancel()
            backgroundTimeoutJob = null
            if (visible) {
                resumedAt = SystemClock.elapsedRealtime()
                val owner = prefs?.baseUrl?.let { "bridge-events:" + connectionScope(it) } ?: return
                scheduleBackgroundTimeoutPromotion(owner)
            }
        }
    }

    private fun scheduleBackgroundTimeoutPromotion(owner: String) {
        if (!activityVisible || backgroundTimeoutJob?.isActive == true) return
        val active = client ?: return
        val wait = (BACKGROUND_EVENT_GRACE_MS - (SystemClock.elapsedRealtime() - resumedAt)).coerceAtLeast(0)
        backgroundTimeoutJob = scope.launch {
            delay(wait)
            synchronized(errorLock) {
                if (!activityVisible || client !== active) return@synchronized
                val updated = promoteBackgroundTimeouts(_notifications.value, owner)
                if (updated == _notifications.value) return@synchronized
                _notifications.value = updated
                val error = updated.lastOrNull { !it.quiet && it.state != "recovered" && it.diagnostic?.owner == owner }?.diagnostic
                if (error != null && (lastDiagnostic == null || lastDiagnostic?.owner == owner)) {
                    lastDiagnostic = error
                    lastErrorKind = BridgeErrorKind.CONNECTION
                    _lastError.value = errorMessage(error)
                }
                persistNotifications()
            }
        }
    }

    private val _savedConnections = MutableStateFlow<List<SavedConnection>>(emptyList())
    val savedConnections = _savedConnections.asStateFlow()
    private val _paired = MutableStateFlow(false)
    val paired = _paired.asStateFlow()
    val language = AppLocale.language

    private val _connection = MutableStateFlow("offline")
    val connection = _connection.asStateFlow()

    private val _sessions = MutableStateFlow<List<SessionSummary>>(emptyList())
    val sessions = _sessions.asStateFlow()

    private val _archiveCandidateCount = MutableStateFlow(0)
    val archiveCandidateCount = _archiveCandidateCount.asStateFlow()

    private val _approvals = MutableStateFlow<List<ApprovalDto>>(emptyList())
    val approvals = _approvals.asStateFlow()

    private val _approvalsEnabled = MutableStateFlow(true)
    val approvalsEnabled = _approvalsEnabled.asStateFlow()

    private val _securitySettings = MutableStateFlow(SecuritySettingsResponse())
    val securitySettings = _securitySettings.asStateFlow()

    private val _securityLevel = MutableStateFlow(SECURITY_LEVEL_DEFAULT)
    val securityLevel = _securityLevel.asStateFlow()

    private val _sandboxSettings = MutableStateFlow(SandboxSettingsResponse())
    val sandboxSettings = _sandboxSettings.asStateFlow()

    private val _devices = MutableStateFlow<List<DeviceDto>>(emptyList())
    val devices = _devices.asStateFlow()

    private val _currentDeviceId = MutableStateFlow("")
    val currentDeviceId = _currentDeviceId.asStateFlow()

    private val _timeline = MutableStateFlow<List<TimelineItem>>(emptyList())
    val timeline = _timeline.asStateFlow()
    private val _contextUsage = MutableStateFlow(ContextUsage())
    val contextUsage = _contextUsage.asStateFlow()
    private val _sessionPlan = MutableStateFlow(SessionPlan())
    val sessionPlan = _sessionPlan.asStateFlow()

    private val _agentSessions = MutableStateFlow(AgentSessionsResponse())
    val agentSessions = _agentSessions.asStateFlow()
    private val _agentSessionsError = MutableStateFlow<String?>(null)
    val agentSessionsError = _agentSessionsError.asStateFlow()

    fun refreshAgentSessions(sessionId: String) {
        val active = client ?: return
        scope.launch {
            runCatching { active.agentSessions(sessionId) }
                .onSuccess { if (sessionId == selectedSessionId && client === active) { _agentSessions.value = it; _agentSessionsError.value = null } }
                .onFailure { if (sessionId == selectedSessionId && client === active) _agentSessionsError.value = it.message }
        }
    }

    private val _sessionDetail = MutableStateFlow<SessionDetail?>(null)
    val sessionDetail = _sessionDetail.asStateFlow()

    private val _lastError = MutableStateFlow<String?>(null)
    val lastError = _lastError.asStateFlow()

    // The notification log persists beyond one session screen: this allows determining whether an error is old or recent based on its last appearance time.
    private val _notifications = MutableStateFlow<List<NotificationEntry>>(emptyList())
    val notifications = _notifications.asStateFlow()

    private val _serverUrl = MutableStateFlow("")
    val serverUrl = _serverUrl.asStateFlow()
    private val _attachments = MutableStateFlow<List<AttachmentDto>>(emptyList())
    val attachments = _attachments.asStateFlow()

    private val _sessionChanges = MutableStateFlow(ChangedFilesResponse())
    val sessionChanges = _sessionChanges.asStateFlow()

    private val _sessionChangesDiff = MutableStateFlow<FileDiffResponse?>(null)
    val sessionChangesDiff = _sessionChangesDiff.asStateFlow()

    private val _sessionCheckpoints = MutableStateFlow<CheckpointListResponse?>(null)
    val sessionCheckpoints = _sessionCheckpoints.asStateFlow()

    private val _checkpointsLoading = MutableStateFlow(false)
    val checkpointsLoading = _checkpointsLoading.asStateFlow()

    private val _checkpointsError = MutableStateFlow<String?>(null)
    val checkpointsError = _checkpointsError.asStateFlow()

    private val _checkpointChanges = MutableStateFlow<ChangedFilesResponse?>(null)
    val checkpointChanges = _checkpointChanges.asStateFlow()

    private val _checkpointChangesLoading = MutableStateFlow(false)
    val checkpointChangesLoading = _checkpointChangesLoading.asStateFlow()

    private val _checkpointChangesError = MutableStateFlow<String?>(null)
    val checkpointChangesError = _checkpointChangesError.asStateFlow()

    private val _checkpointDiff = MutableStateFlow<FileDiffResponse?>(null)
    val checkpointDiff = _checkpointDiff.asStateFlow()

    private val _extensions = MutableStateFlow(ExtensionsResponse())
    val extensions = _extensions.asStateFlow()

    private val _externalSessions = MutableStateFlow(ExternalSessionsResponse())
    val externalSessions = _externalSessions.asStateFlow()

    private val _externalSessionDetail = MutableStateFlow<ExternalSessionDetail?>(null)
    val externalSessionDetail = _externalSessionDetail.asStateFlow()

    // OpenCode subscription quota is the same for the web panel and mobile app.
    private val _quota = MutableStateFlow<QuotaState>(QuotaState.Idle)
    internal val quota = _quota.asStateFlow()
    private val quotaRefresh = Mutex()
    private var quotaProfile = ""
    private fun currentQuotaProfile(): String = _sessions.value.find { it.sessionId == selectedSessionId }?.profile.orEmpty().ifBlank { (_sessionDetail.value?.meta?.get("profile") as? JsonPrimitive)?.content.orEmpty() }

    /**
     * Updates the OpenCode subscription quota if the data is outdated.
     *
     * Without [force], the request is only sent when there are no fresh data in the cache (the same
     * 60 seconds as on the server) — tapping the circle in the header does not trigger upstream.
     */
    fun refreshQuota(force: Boolean = false) {
        val active = client ?: return
        val requestedProfile = currentQuotaProfile()
        if (quotaProfile != requestedProfile) { quotaProfile = requestedProfile; _quota.value = QuotaState.Idle }
        val current = _quota.value
        if (!force) {
            if (current is QuotaState.Loading) return
            if (current is QuotaState.Ready &&
                quotaCacheFresh(current.fetchedAtMillis, System.currentTimeMillis())
            ) {
                return
            }
        }
        scope.launch {
            if (!quotaRefresh.tryLock()) return@launch
            try {
                if (_quota.value !is QuotaState.Ready) _quota.value = QuotaState.Loading
                val result = runCatching { active.quota(refresh = force, profile = requestedProfile).also { if (it.quotaError != null) throw BridgeException(400, "Quota unavailable", it.quotaError.code); if (it.provider == "unsupported") throw BridgeException(400, "Quota unavailable", "unsupported") } }
                result.onSuccess {
                    if (client === active && quotaProfile == requestedProfile) {
                        _quota.value = QuotaState.Ready(
                            usage = it.usage ?: QuotaUsageDto(), provider = it.provider,
                            fetchedAtMillis = parseInstantMillis(it.fetchedAt) ?: System.currentTimeMillis(),
                        )
                        clearRecoveredConnectionError()
                    }
                }
                result.onFailure {
                    // A quota error does not affect the overall connection status or notification feed:
                    // this is a local state of the card.
                    if (client === active && quotaProfile == requestedProfile) _quota.value = QuotaState.Failed(quotaFailureCode(it))
                }
            } finally {
                quotaRefresh.unlock()
                if (client === active && quotaProfile != requestedProfile) refreshQuota()
            }
        }
    }

    private fun quotaFailureCode(error: Throwable): String = when {
        error is BridgeException -> error.code ?: "upstream"
        error is IOException -> "network"
        else -> "upstream"
    }

    internal val voiceEvents = MutableSharedFlow<BridgeEvent>(extraBufferCapacity = 256)

    val alerts = MutableSharedFlow<Alert>(extraBufferCapacity = 64)

    fun init(context: Context) {
        if (prefs != null) return
        val store = Prefs(context.applicationContext)
        prefs = store
        loadNotificationHistory()
        runCatching {
            val manager=context.applicationContext.getSystemService(ConnectivityManager::class.java)
            networkAvailable=manager.activeNetwork!=null
            val callback=object:ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network:Network) { networkAvailable=true; networkWake.trySend(Unit) }
                override fun onLost(network:Network) { networkAvailable=manager.activeNetwork!=null; networkWake.trySend(Unit) }
            }
            connectivityCallback=callback
            manager.registerDefaultNetworkCallback(callback)
        }
        store.saveCurrentConnection()
        _savedConnections.value = store.savedConnections()
        _screenshotsBlocked.value = store.screenshotsBlocked
        AppLocale.set(AppLanguage.fromCode(store.appLanguage))
        _serverUrl.value = store.baseUrl
        client = if (store.isPaired()) runCatching { buildClient(store) }.onFailure {
            setLastError(it.message ?: "Для подключения требуется HTTPS", BridgeErrorKind.OPERATION)
        }.getOrNull() else null
        _paired.value = client != null
    }

    fun setLanguage(language: AppLanguage) {
        prefs?.appLanguage = language.code
        AppLocale.set(language)
        synchronized(timelineLock) { timelines.clear() }
        _timeline.value = emptyList()
        _contextUsage.value = ContextUsage()
        _sessionPlan.value = SessionPlan()
        selectedSessionId?.let(::openSession)
    }

    private fun buildClient(store: Prefs): BridgeClient =
        instrumentClient(BridgeClient(store.baseUrl, store.token, store.fingerprint.ifBlank { null }))

    private fun instrumentClient(active: BridgeClient): BridgeClient = active.also {
        active.onDiagnostic={ error -> if(client===active) {
            synchronized(errorLock) { if(error.owner==active.transportOwner)httpReadySince=0 }
            recordDiagnostic(error)
        }}
        active.onSuccess={ if(client===active) clearRecoveredConnectionError() }
        active.onRecovered={ owner -> if(client===active)recoverDiagnostic(owner,0,operationCompleted=true) }
    }

    fun normalizeUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/')
        if (trimmed.isEmpty()) return ""
        return secureBridgeUrl(trimmed)
    }

    fun pair(url: String, code: String, name: String, fingerprint: String, label: String = "", onDone: (Throwable?) -> Unit) {
        val store = prefs ?: return
        if (ru.igorek.remote.service.VoiceCalls.state.value.running) { onDone(IOException("Сначала завершите голосовой разговор")); return }
        scope.launch {
            try {
                val normalized = normalizeUrl(url)
                val probe = BridgeClient(normalized, token = "", pinnedFingerprint = fingerprint.ifBlank { null })
                val result = probe.pair(normalized, code, name)
                store.saveCurrentConnection()
                clearConnectionState()
                store.activeConnectionId = ""
                store.baseUrl = normalized
                store.token = result.token
                store.fingerprint = fingerprint.trim()
                store.deviceName = name
                store.lastSeq = 0
                store.lastSessionProfile = ""; store.lastSessionCwd = ""
                store.saveCurrentConnection(label.ifBlank { "Игорёк · $normalized" })
                _savedConnections.value = store.savedConnections()
                _serverUrl.value = normalized
                clearLastError()
                client = buildClient(store)
                _paired.value = true
                refreshSessions()
                refreshApprovalSettings()
                refreshSecuritySettings()
                refreshSandboxSettings()
                startStream()
                withContext(Dispatchers.Main) { onDone(null) }
            } catch (error: Throwable) {
                setLastError(error.message ?: error.javaClass.simpleName, bridgeErrorKind(error))
                withContext(Dispatchers.Main) { onDone(error) }
            }
        }
    }

    fun unpair() {
        clearConnectionState()
        prefs?.clear()
        _savedConnections.value = emptyList()
        _paired.value = false
    }

    private fun clearConnectionState() {
        backgroundTimeoutJob?.cancel()
        backgroundTimeoutJob = null
        stopStream()
        sessionGeneration.incrementAndGet()
        selectedSessionId = null
        archivedOnly = false
        pendingMessages.clear()
        client = null
        _serverUrl.value = ""
        _sessions.value = emptyList()
        _archiveCandidateCount.value = 0
        _approvals.value = emptyList()
        _devices.value = emptyList(); _currentDeviceId.value = ""
        _notifications.value = emptyList(); replayThrough = 0
        _approvalsEnabled.value = true
        _securitySettings.value = SecuritySettingsResponse()
        _securityLevel.value = SECURITY_LEVEL_DEFAULT
        _sandboxSettings.value = SandboxSettingsResponse()
        _timeline.value = emptyList()
        _contextUsage.value = ContextUsage()
        _sessionPlan.value = SessionPlan()
        _quota.value = QuotaState.Idle; quotaProfile = ""
        _sessionDetail.value = null
        _attachments.value = emptyList()
        _sessionChanges.value = ChangedFilesResponse()
        _sessionChangesDiff.value = null
        clearSessionCheckpoints()
        _extensions.value = ExtensionsResponse()
        _externalSessions.value = ExternalSessionsResponse()
        _externalSessionDetail.value = null
        clearLastError()
        synchronized(timelineLock) { timelines.clear() }
    }

    fun refreshSessions() {
        val active = client ?: return
        scope.launch {
            if (!sessionsRefresh.tryLock()) return@launch
            try {
                runCatching { active.sessionListing(archivedOnly) }
                    .onSuccess {
                        if (client === active) {
                            clearRecoveredConnectionError()
                            _sessions.value = it.sessions.map { session ->
                                withMcpSessionSummary(session, runCatching { mcpChat(session.sessionId) }.getOrDefault(emptyList()))
                            }
                            _archiveCandidateCount.value = it.archiveCandidateCount
                        }
                    }
                    .onFailure { if (client === active) handleFailure(it) }
            } finally { sessionsRefresh.unlock() }
        }
    }

    fun setArchivedFilter(value: Boolean) {
        if (archivedOnly == value) return
        archivedOnly = value
        refreshSessions()
    }

    fun renameSession(sessionId: String, title: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.updateSession(sessionId, title = title) }
            result.onFailure { handleFailure(it) }
            if (result.isSuccess) refreshSessions()
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun archiveSession(sessionId: String, archived: Boolean, onDone: (Throwable?) -> Unit) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.updateSession(sessionId, archived = archived) }
            result.onFailure { handleFailure(it) }
            if (result.isSuccess) refreshSessions()
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun archiveAllSessions(onDone: (ArchiveSessionsResponse?, Throwable?) -> Unit) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.archiveSessions() }
            result.onSuccess { response ->
                val archivedIds = response.sessionIds.toSet()
                _sessions.value = _sessions.value.filterNot { it.sessionId in archivedIds }
                _archiveCandidateCount.value = 0
                refreshSessions()
            }
            result.onFailure { handleFailure(it) }
            withContext(Dispatchers.Main) {
                onDone(result.getOrNull(), result.exceptionOrNull())
            }
        }
    }

    fun createSession(profile: String, cwd: String, key: String, onDone: (String?, Throwable?) -> Unit) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.createSession(profile.trim(), cwd.trim(), key) }
            result.onFailure { handleFailure(it) }
            if (result.isSuccess) {
                prefs?.lastSessionProfile = profile.trim()
                prefs?.lastSessionCwd = cwd.trim()
                refreshSessions()
            }
            withContext(Dispatchers.Main) { onDone(result.getOrNull()?.sessionId, result.exceptionOrNull()) }
        }
    }

    fun switchSessionProfile(sessionId: String, profile: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return onDone(IOException(AppLocale.text("Нет подключения к серверу", "No server connection")))
        if (!profileRouteGate.beginSwitch()) return onDone(IOException(AppLocale.text(
            "Дождитесь завершения отправки или переключения профиля", "Wait for sending or profile switching to finish")))
        _profileSwitching.value = true
        val generation = sessionGeneration.get()
        clearLastError()
        scope.launch {
            val result = try {
                val switched = runCatching { active.switchSessionProfile(sessionId, profile) }
                    .map { detail ->
                        check(detail.sessionId == sessionId && (detail.meta["profile"] as? JsonPrimitive)?.content == profile) {
                            "Unexpected profile switch response"
                        }
                        detail
                    }
                if (client === active) {
                    switched.onSuccess { detail ->
                        val model = (detail.meta["model"] as? JsonPrimitive)?.content.orEmpty().ifBlank { detail.model }
                        _sessions.update { entries -> entries.map { entry -> if (entry.sessionId == sessionId)
                            entry.copy(profile = profile, model = model) else entry } }
                        if (selectedSessionId == sessionId && sessionGeneration.get() == generation) {
                            sessionGeneration.incrementAndGet()
                            _sessionDetail.value = detail
                            refreshExtensions(profile)
                        }
                        refreshSessions()
                    }.onFailure { handleFailure(it) }
                }
                switched
            } finally {
                profileRouteGate.endSwitch()
                _profileSwitching.value = false
            }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun loadSessionOptions(onDone: (SessionOptionsResponse?, Throwable?) -> Unit) {
        val active = client ?: return onDone(null, IOException(AppLocale.text("Нет подключения к серверу", "No server connection")))
        clearLastError()
        scope.launch {
            val result = runCatching { active.sessionOptions() }
                .map { options ->
                    val savedProfile = prefs?.lastSessionProfile.orEmpty()
                    val savedCwd = prefs?.lastSessionCwd.orEmpty()
                    options.copy(
                        defaultProfile = savedProfile.takeIf { it in options.selectableProfiles }
                            ?: options.defaultProfile,
                        defaultCwd = savedCwd.takeIf { it in options.workdirs }
                            ?: options.defaultCwd,
                    )
                }
            result.onFailure { handleFailure(it) }
            withContext(Dispatchers.Main) {
                onDone(result.getOrNull(), result.exceptionOrNull())
            }
        }
    }

    fun refreshExternalSessions(refresh: Boolean = false, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.externalSessions(refresh) }
            result.onSuccess {
                if (client === active) {
                    _externalSessions.value = it
                    clearRecoveredConnectionError()
                }
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun openExternalSession(source: String, sessionId: String, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        clearLastError()
        _externalSessionDetail.value = null
        scope.launch {
            val result = runCatching { active.externalSession(source, sessionId) }
            result.onSuccess {
                if (client === active) _externalSessionDetail.value = it
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun closeExternalSession() {
        _externalSessionDetail.value = null
    }

    fun loadMoreExternalSession(onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        val current = _externalSessionDetail.value ?: return
        val offset = current.nextOffset ?: return
        scope.launch {
            val result = runCatching { active.externalSession(current.source, current.sessionId, offset) }
            result.onSuccess { page ->
                if (client === active && _externalSessionDetail.value?.sessionId == current.sessionId) {
                    _externalSessionDetail.value = current.copy(
                        messages = current.messages + page.messages,
                        totalMessages = page.totalMessages,
                        nextOffset = page.nextOffset,
                    )
                }
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun importExternalSession(
        source: String,
        sessionId: String,
        profile: String,
        cwd: String,
        key: String,
        onDone: (String?, ImportExternalSessionResponse?, Throwable?) -> Unit,
    ) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching {
                active.importExternalSession(source, sessionId, profile.trim(), cwd.trim(), key)
            }
            result.onSuccess {
                prefs?.lastSessionProfile = profile.trim()
                // Empty cwd must not overwrite the saved new-chat workspace.
                if (cwd.isNotBlank()) prefs?.lastSessionCwd = cwd.trim()
                refreshSessions()
            }
            result.onFailure { handleFailure(it) }
            withContext(Dispatchers.Main) {
                val response = result.getOrNull()
                onDone(response?.session?.sessionId, response, result.exceptionOrNull())
            }
        }
    }

    fun refreshExtensions(profile: String, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        val requestedProfile = profile.trim()
        if (requestedProfile.isBlank()) return
        scope.launch {
            val result = runCatching { active.extensions(requestedProfile) }
            result.onSuccess { response ->
                if (client === active && response.profile == requestedProfile) {
                    _extensions.value = response
                    clearRecoveredConnectionError()
                }
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun setSkillEnabled(skillId: String, enabled: Boolean, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        val profile = _extensions.value.profile
        scope.launch {
            val result = runCatching { active.setSkillEnabled(skillId, enabled) }
            result.onSuccess { response ->
                _extensions.value = _extensions.value.copy(
                    skills = _extensions.value.skills.map {
                        if (it.skillId == response.skill.skillId) response.skill.copy(supported = it.supported) else it
                    },
                )
                if (profile.isNotBlank()) refreshExtensions(profile)
            }
            result.onFailure { handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun refreshApprovals() {
        val active = client ?: return
        scope.launch {
            if (!approvalsRefresh.tryLock()) return@launch
            try {
                runCatching { active.approvals(null) }
                    .onSuccess {
                        if (client === active) {
                            clearRecoveredConnectionError()
                            _approvals.value = it.filterNot { approval ->
                                approval.kind != "user_input" && isApprovalAlreadyResolved(approval.taskId, approval.requestId)
                            }
                        }
                    }
                    .onFailure { if (client === active) handleFailure(it) }
            } finally { approvalsRefresh.unlock() }
        }
    }

    fun refreshApprovalSettings() {
        val active = client ?: return
        scope.launch {
            runCatching { active.approvalSettings() }
                .onSuccess {
                    if (client === active) {
                        _approvalsEnabled.value = it.enabled
                        clearRecoveredConnectionError()
                    }
                }
                .onFailure { if (client === active) handleFailure(it) }
        }
    }

    fun setApprovalsEnabled(enabled: Boolean, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.setApprovalSettings(enabled) }
            result.onSuccess {
                if (client === active) {
                    _approvalsEnabled.value = it.enabled
                    refreshApprovals()
                    refreshSessions()
                }
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun refreshSecuritySettings(onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        scope.launch {
            val result = runCatching { active.securitySettings() }
            result.onSuccess {
                if (client === active) {
                    applySecuritySettings(it)
                    clearRecoveredConnectionError()
                }
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun setSecurityLevel(
        level: String,
        ttlS: Long? = null,
        confirm: Boolean = false,
        onDone: (Throwable?) -> Unit = {},
    ) {
        val active = client ?: return
        val target = normalizeSecurityLevel(level)
        if (securityChangeNeedsConfirmation(target, _securitySettings.value) && !confirm) {
            onDone(IOException(AppLocale.text(
                "Ослабление уровня требует подтверждения",
                "Weakening the access level requires confirmation",
            )))
            return
        }
        clearLastError()
        scope.launch {
            val result = runCatching { active.setSecurityLevel(target, ttlS, confirm) }
            result.onSuccess { if (client === active) applySecuritySettings(it) }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    private fun applySecuritySettings(settings: SecuritySettingsResponse) {
        _securitySettings.value = settings
        _securityLevel.value = normalizeSecurityLevel(settings.level)
    }

    fun refreshSandboxSettings(onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        scope.launch {
            val result = runCatching { active.sandboxSettings() }
            result.onSuccess {
                if (client === active) {
                    _sandboxSettings.value = it
                    clearRecoveredConnectionError()
                }
            }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun setSandboxEnabled(enabled: Boolean, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.setSandboxEnabled(enabled) }
            result.onSuccess { if (client === active) _sandboxSettings.value = it }
            result.onFailure { if (client === active) handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun openSession(sessionId: String) {
        val active = client ?: return
        clearLastError()
        val generation = sessionGeneration.incrementAndGet()
        val checkpointsWereLoaded = _sessionCheckpoints.value != null
        selectedSessionId = sessionId
        _agentSessions.value = AgentSessionsResponse()
        _agentSessionsError.value = null
        refreshAgentSessions(sessionId)
        _sessionDetail.value = null
        _attachments.value = emptyList()
        _sessionChanges.value = ChangedFilesResponse()
        _sessionChangesDiff.value = null
        clearSessionCheckpoints()
        refreshFiles(sessionId)
        refreshSessionChanges(sessionId)
        if (checkpointsWereLoaded) refreshSessionCheckpoints(sessionId)
        val accumulator = synchronized(timelineLock) { timelines.getOrPut(sessionId) { SessionTimeline(sessionId) } }
        _timeline.value = accumulator.snapshot()
        _contextUsage.value = accumulator.contextSnapshot()
        _sessionPlan.value = accumulator.planSnapshot()
        scope.launch {
            runCatching {
                var cursor = 0L
                while (sessionGeneration.get() == generation) {
                    val events = active.sessionEvents(sessionId, afterSeq = cursor)
                    if (sessionGeneration.get() != generation) return@launch
                    events.forEach { accumulator.apply(it) }
                    _timeline.value = accumulator.snapshot()
                    _contextUsage.value = accumulator.contextSnapshot()
                    _sessionPlan.value = accumulator.planSnapshot()
                    val next = events.mapNotNull { it.seq }.maxOrNull() ?: break
                    if (next <= cursor || events.size < 500) break
                    cursor = next
                }
            }.onFailure { if (sessionGeneration.get() == generation) handleFailure(it) }
            runCatching { active.session(sessionId) }
                .onSuccess { detail ->
                    if (sessionGeneration.get() != generation) return@onSuccess
                    _sessionDetail.value = detail
                    accumulator.seedFromHistory(detail.messages)
                    _timeline.value = accumulator.snapshot()
                    _contextUsage.value = accumulator.contextSnapshot()
                    _sessionPlan.value = accumulator.planSnapshot()
                }
                .onFailure { if (sessionGeneration.get() == generation) handleFailure(it) }
        }
    }

    fun refreshFiles(sessionId: String) {
        val active = client ?: return
        scope.launch {
            if (!filesRefresh.tryLock()) return@launch
            try {
                val files = active.attachments(sessionId)
                if (client === active && selectedSessionId == sessionId) _attachments.value = files
            } catch (error: Exception) {
                if (client === active && selectedSessionId == sessionId) handleFailure(error)
            } finally { filesRefresh.unlock() }
        }
    }

    // List of modified files — auxiliary information for the chip in the header.
    // Old bridge build endpoints do not return data, so this error is not raised into the general banner:
    // chat continues to function without statistics.
    fun refreshSessionChanges(sessionId: String) {
        val active = client ?: return
        scope.launch {
            if (!changesRefresh.tryLock()) return@launch
            try {
                val changes = active.sessionChanges(sessionId)
                if (client === active && selectedSessionId == sessionId) _sessionChanges.value = changes
            } catch (_: Exception) {
                // The endpoint may be absent; the chip remains hidden.
            } finally { changesRefresh.unlock() }
        }
    }

    // File diff for a dialog. Errors are suppressed and returned as source=unavailable,
    // so the UI displays 'diff unavailable' instead of an empty screen.
    fun loadSessionChangesDiff(sessionId: String, path: String) {
        pendingDiffPath = path
        _sessionChangesDiff.value = null
        val active = client
        if (active == null) {
            _sessionChangesDiff.value = FileDiffResponse(path = path, source = "unavailable")
            return
        }
        scope.launch {
            diffRefresh.lock()
            try {
                val diff = active.sessionChangesDiff(sessionId, path)
                if (client === active && selectedSessionId == sessionId && pendingDiffPath == path) {
                    _sessionChangesDiff.value = diff
                }
            } catch (_: Exception) {
                if (client === active && selectedSessionId == sessionId && pendingDiffPath == path) {
                    _sessionChangesDiff.value = FileDiffResponse(path = path, source = "unavailable")
                }
            } finally {
                diffRefresh.unlock()
            }
        }
    }

    fun clearSessionChangesDiff() {
        pendingDiffPath = null
        _sessionChangesDiff.value = null
    }

    suspend fun browserPreview(after: String = ""): BrowserPreviewResponse {
        val active = client ?: throw BridgeException(401, "Not connected")
        val result = active.browserPreview(after)
        if (client !== active) throw BridgeException(409, "Connection changed")
        return result
    }

    suspend fun selectBrowserTab(tabId: String): BrowserTabSelectionResponse {
        val active = client ?: throw BridgeException(401, "Not connected")
        val result = active.selectBrowserTab(tabId)
        if (client !== active) throw BridgeException(409, "Connection changed")
        return result
    }

    // Checkpoint errors remain inside the versions panel for older bridges.
    fun refreshSessionCheckpoints(sessionId: String, limit: Int = 50, offset: Int = 0) {
        val active = client ?: return
        scope.launch {
            if (!checkpointsRefresh.tryLock()) return@launch
            try {
                if (selectedSessionId != sessionId) return@launch
                _checkpointsLoading.value = true
                _checkpointsError.value = null
                runCatching { active.sessionCheckpoints(sessionId, limit, offset) }
                    .onSuccess { response ->
                        if (client === active && selectedSessionId == sessionId) {
                            _sessionCheckpoints.value = response
                        }
                    }
                    .onFailure { error ->
                        if (client === active && selectedSessionId == sessionId) {
                            _checkpointsError.value = error.message?.takeIf { it.isNotBlank() }
                                ?: AppLocale.text("Не удалось загрузить историю версий", "Could not load the version history")
                        }
                    }
            } finally {
                if (selectedSessionId == sessionId) _checkpointsLoading.value = false
                checkpointsRefresh.unlock()
            }
        }
    }

    fun openCheckpointChanges(hash: String) {
        val active = client ?: return
        val sessionId = selectedSessionId ?: return
        selectedCheckpointHash = hash
        pendingCheckpointDiffPath = null
        _checkpointChanges.value = null
        _checkpointDiff.value = null
        _checkpointChangesError.value = null
        scope.launch {
            checkpointChangesRefresh.lock()
            try {
                _checkpointChangesLoading.value = true
                val changes = active.checkpointChanges(sessionId, hash)
                if (client === active && selectedSessionId == sessionId && selectedCheckpointHash == hash) {
                    _checkpointChanges.value = changes
                }
            } catch (error: Exception) {
                if (client === active && selectedSessionId == sessionId && selectedCheckpointHash == hash) {
                    _checkpointChangesError.value = error.message?.takeIf { it.isNotBlank() }
                        ?: AppLocale.text("Не удалось загрузить файлы версии", "Could not load the files of this version")
                }
            } finally {
                if (selectedCheckpointHash == hash) _checkpointChangesLoading.value = false
                checkpointChangesRefresh.unlock()
            }
        }
    }

    fun clearCheckpointChanges() {
        selectedCheckpointHash = null
        _checkpointChanges.value = null
        _checkpointChangesError.value = null
        _checkpointChangesLoading.value = false
    }

    // File diff between the checkpoint and current state. Similar to /changes/diff:
    // an error converts to source=unavailable, so the dialog explains the emptiness.
    fun openCheckpointDiff(hash: String, path: String) {
        pendingCheckpointDiffPath = path
        _checkpointDiff.value = null
        val sessionId = selectedSessionId
        val active = client
        if (active == null || sessionId == null) {
            _checkpointDiff.value = FileDiffResponse(path = path, source = "unavailable", from = hash)
            return
        }
        scope.launch {
            checkpointDiffRefresh.lock()
            try {
                val diff = active.checkpointDiff(sessionId, hash, path)
                if (client === active && selectedSessionId == sessionId && pendingCheckpointDiffPath == path) {
                    _checkpointDiff.value = diff
                }
            } catch (_: Exception) {
                if (client === active && selectedSessionId == sessionId && pendingCheckpointDiffPath == path) {
                    _checkpointDiff.value = FileDiffResponse(path = path, source = "unavailable", from = hash)
                }
            } finally {
                checkpointDiffRefresh.unlock()
            }
        }
    }

    fun clearCheckpointDiff() {
        pendingCheckpointDiffPath = null
        _checkpointDiff.value = null
    }

    fun clearSessionCheckpoints() {
        selectedCheckpointHash = null
        pendingCheckpointDiffPath = null
        _sessionCheckpoints.value = null
        _checkpointsLoading.value = false
        _checkpointsError.value = null
        _checkpointChanges.value = null
        _checkpointChangesLoading.value = false
        _checkpointChangesError.value = null
        _checkpointDiff.value = null
    }

    private val mcpChatHistory = McpChatHistory(
        read = { sessionId -> prefs?.mcpChat(sessionId).orEmpty() },
        write = { sessionId, encoded -> checkNotNull(prefs).saveMcpChat(sessionId, encoded) },
    )

    internal fun mcpChat(sessionId: String): List<McpChatRow> = mcpChatHistory.load(sessionId)
    internal fun saveMcpChat(sessionId: String, rows: List<McpChatRow>) {
        mcpChatHistory.save(sessionId, rows)
        _sessions.update { sessions ->
            sessions.map { if (it.sessionId == sessionId) withMcpSessionSummary(it, rows) else it }
        }
    }

    private val mcpMutex = Mutex()

    internal suspend fun mcpRequest(action: String, fields: kotlinx.serialization.json.JsonObject = kotlinx.serialization.json.buildJsonObject {}): kotlinx.serialization.json.JsonObject = withContext(Dispatchers.IO) {
        mcpMutex.lock()
        try { (client ?: throw IOException("Not connected")).mcp(action, fields) }
        finally { mcpMutex.unlock() }
    }

    internal fun clearMcpAsync() = scope.launch(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) { runCatching { mcpRequest("clear") } }

    internal fun schedulesConnection(): SchedulesConnection {
        val active = client ?: throw IOException("Нет подключения к серверу")
        return SchedulesConnection(active) { client === active }
    }

    internal suspend fun voiceRequest(sessionId: String, path: String, body: String?): kotlinx.serialization.json.JsonObject = withContext(Dispatchers.IO) {
        val active = client ?: throw IOException("Not connected")
        val reply = active.voiceRequest(voiceRoute(sessionId, path), body)
        if (client === active) {
            voiceApprovalDecision(path, body)?.let { decision ->
                markResolvedLocally(decision.taskId, decision.requestId, decision.decision, sessionId)
                refreshApprovals()
                refreshSessions()
            }
        }
        reply
    }

    internal fun voiceRequestAsync(sessionId: String, path: String, body: String?, callback: (Result<kotlinx.serialization.json.JsonObject>) -> Unit) {
        scope.launch { callback(runCatching { voiceRequest(sessionId, path, body) }) }
    }

    internal fun voiceHangup(sessionId: String, callId: String) {
        if (callId.isBlank()) return
        scope.launch { runCatching { voiceRequest(sessionId, "/api/voice/hangup", org.json.JSONObject().put("call_id", callId).toString()) } }
    }

    internal suspend fun changesReview(sessionId: String, mode: String, base: String, untracked: Boolean): ChangesReview = withContext(Dispatchers.IO) {
        (client ?: throw IOException("Not connected")).changesReview(sessionId, reviewQuery(mode, base, untracked))
    }

    internal suspend fun changesReviewFile(sessionId: String, mode: String, base: String, untracked: Boolean, path: String, revision: String): ReviewFile = withContext(Dispatchers.IO) {
        (client ?: throw IOException("Not connected")).changesReviewFile(sessionId, reviewQuery(mode, base, untracked, path, revision))
    }

    suspend fun terminalTicket(sessionId: String): String = withContext(Dispatchers.IO) {
        (client ?: throw IOException("Not connected")).terminalTicket(sessionId)
    }

    fun terminalSocket(listener: okhttp3.WebSocketListener): okhttp3.WebSocket =
        (client ?: throw IOException("Not connected")).terminalSocket(listener)

    suspend fun download(sessionId: String, file: AttachmentDto, output: OutputStream, progress: (Long) -> Unit) {
        val active = client ?: throw IOException(AppLocale.text("Нет подключения к серверу", "No server connection"))
        withContext(Dispatchers.IO) { active.download(sessionId, file, output, progress) }
    }

    fun switchConnection(id: String, onDone: (Throwable?) -> Unit) {
        val store = prefs ?: return
        if (ru.igorek.remote.service.VoiceCalls.state.value.running) {
            onDone(IOException("Сначала завершите голосовой разговор")); return
        }
        val saved = store.connection(id) ?: return onDone(IOException("Подключение не найдено"))
        if (id == store.activeConnectionId) return onDone(null)
        scope.launch {
            val result = runCatching {
                val probe = BridgeClient(saved.url, saved.token, saved.fingerprint.ifBlank { null })
                val listing = probe.sessionListing()
                store.saveCurrentConnection()
                clearConnectionState()
                store.activateConnection(id)
                client = instrumentClient(probe); _paired.value = true; _serverUrl.value = saved.url
                loadNotificationHistory()
                _savedConnections.value = store.savedConnections()
                _sessions.value = listing.sessions; _archiveCandidateCount.value = listing.archiveCandidateCount
                refreshApprovals(); refreshApprovalSettings(); refreshSecuritySettings(); refreshSandboxSettings()
                startStream()
            }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun changeServer(url: String, onDone: (Throwable?) -> Unit) {
        val store = prefs ?: return
        scope.launch {
            val result = runCatching {
                val normalized = normalizeUrl(url)
                val probe = BridgeClient(normalized, store.token, store.fingerprint.ifBlank { null })
                val listing = probe.sessionListing()
                stopStream()
                client = instrumentClient(probe)
                store.baseUrl = normalized
                store.saveCurrentConnection()
                _savedConnections.value = store.savedConnections()
                loadNotificationHistory()
                _serverUrl.value = normalized
                _sessions.value = listing.sessions
                _archiveCandidateCount.value = listing.archiveCandidateCount
                clearLastError()
                startStream()
                refreshApprovals()
            }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun sendMessage(
        sessionId: String,
        text: String,
        uploadIds: List<String> = emptyList(),
        attachmentNames: List<String> = emptyList(),
        uploadedAttachments: List<UploadAttachmentDto> = emptyList(),
        skillIds: List<String> = emptyList(),
        onDone: (Throwable?) -> Unit,
    ) {
        val active = client ?: return
        if (!profileRouteGate.beginSend()) return onDone(IOException(AppLocale.text(
            "Дождитесь переключения профиля", "Wait for profile switching to finish")))
        clearLastError()
        val messageKey = connectionScope(diagnosticJson.encodeToString(listOf(listOf(sessionId, text), uploadIds, skillIds)))
        val key = pendingMessages.getOrPut(messageKey) { "android-${UUID.randomUUID()}" }
        persistPendingOperations()
        scope.launch {
            runCatching {
                try { active.sendMessage(sessionId, text, key, uploadIds, skillIds) }
                catch(error:DiagnosticIOException) {
                    if(error.diagnostic.outcome!="unknown")throw error
                    val lookup=runCatching { active.operationReceipt(key,sessionId) }.getOrNull()
                    val confirmed=lookup ?: throw error
                    val task=(confirmed["task_id"] as? JsonPrimitive)?.content.orEmpty()
                    if((confirmed["recorded"] as? JsonPrimitive)?.booleanOrNull!=true || !task.matches(Regex("[A-Za-z0-9_-]{8,160}")) ||
                        (confirmed["session_id"] as? JsonPrimitive)?.content!=sessionId || (confirmed["kind"] as? JsonPrimitive)?.content!="message")throw error
                    synchronized(errorLock) {
                        _notifications.value=confirmNotifications(_notifications.value,key,task,System.currentTimeMillis());persistNotifications()
                        if(lastDiagnostic?.clientOperationId==key){lastDiagnostic=null;lastErrorKind=null;_lastError.value=null}
                    }
                    val receipt=runCatching { lookup?.get("receipt")?.let { diagnosticJson.decodeFromString<SendMessageResponse>(it.toString()) } }.getOrNull()
                    (receipt ?: SendMessageResponse(taskId=task,status="queued")).copy(taskId=task,duplicate=true)
                }
            }
                .onSuccess { response ->
                    pendingMessages.remove(messageKey, key)
                    persistPendingOperations()
                    val attachmentLine = if (attachmentNames.isEmpty() || uploadedAttachments.isNotEmpty()) ""
                    else AppLocale.text(
                        "[вложения: ${attachmentNames.joinToString(", ")}]",
                        "[attachments: ${attachmentNames.joinToString(", ")}]",
                    )
                    val display = when {
                        text.isBlank() && attachmentLine.isNotEmpty() -> attachmentLine
                        attachmentLine.isEmpty() -> text
                        else -> "$text\n\n$attachmentLine"
                    }
                    addLocal(
                        sessionId,
                        TimelineItem.UserText(
                            userMessageItemId(response.taskId, response.messageId),
                            display,
                            skillIds,
                            queued = response.steered,
                            taskId = response.taskId,
                            messageId = response.messageId,
                            attachments = uploadedAttachments,
                        ),
                    )
                    refreshSessions()
                }
                .onFailure { handleFailure(it) }
                .also { result -> withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) } }
        }.invokeOnCompletion { profileRouteGate.endSend() }
    }

    fun sendMessageNow(sessionId: String, taskId: String, messageId: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return onDone(IOException(AppLocale.text("Нет подключения к серверу", "No server connection")))
        scope.launch {
            val result = runCatching { active.sendMessageNow(sessionId, taskId, messageId) }
            var failure = result.exceptionOrNull()
            val conflict = (failure as? BridgeException)?.status?.let { it == 409 || it == 404 } == true
            if (conflict && resyncQueueState(sessionId, messageId)) {
                // When the server responds with 'message no longer in the queue,' it has been moved to a task
                // or the task has completed. Since the server is the source of truth, we display the updated state
                // instead of an error.
                failure = null
            }
            if (failure != null) handleFailure(failure)
            withContext(Dispatchers.Main) {
                if (result.isSuccess) {
                    val accumulator = synchronized(timelineLock) { timelines[sessionId] }
                    accumulator?.markPrioritized(taskId, messageId)
                    if (sessionId == selectedSessionId && accumulator != null) {
                        _timeline.value = accumulator.snapshot()
                        _contextUsage.value = accumulator.contextSnapshot()
                        _sessionPlan.value = accumulator.planSnapshot()
                    }
                }
                onDone(failure)
            }
        }
    }

    /**
     * Re-reads the actual session queue from the server.
     *
     * `true` — the 'in queue' tag is removed: the message no longer waits for delivery.
     */
    private suspend fun resyncQueueState(sessionId: String, messageId: String): Boolean {
        val active = client ?: return false
        val detail = runCatching { active.session(sessionId) }.getOrNull() ?: return false
        val queuedIds = detail.messages
            .filter { it.queued }
            .mapNotNull { it.messageId?.takeIf { id -> id.isNotBlank() } }
            .toSet()
        val accumulator = synchronized(timelineLock) { timelines[sessionId] } ?: return false
        accumulator.syncQueueState(queuedIds)
        val queued = accumulator.isMessageQueued(messageId)
        withContext(Dispatchers.Main) {
            _sessionDetail.value = detail
            if (sessionId == selectedSessionId) {
                _timeline.value = accumulator.snapshot()
                _contextUsage.value = accumulator.contextSnapshot()
                _sessionPlan.value = accumulator.planSnapshot()
            }
        }
        return !queued
    }

    fun cancelMessage(sessionId: String, taskId: String, messageId: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return onDone(IOException(AppLocale.text("Нет подключения к серверу", "No server connection")))
        scope.launch {
            val result = runCatching { active.cancelMessage(sessionId, taskId, messageId) }
            result.onFailure { handleFailure(it) }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun uploadAttachment(
        sessionId: String,
        data: ByteArray,
        name: String,
        mimeType: String,
        onDone: (String?, Throwable?) -> Unit,
    ) {
        val active = client
        if (active == null) {
            onDone(null, IOException(AppLocale.text("Нет подключения к серверу", "No server connection")))
            return
        }
        clearLastError()
        scope.launch {
            val result = runCatching { active.upload(sessionId, data, name, mimeType) }
            result.onFailure { handleFailure(it) }
            withContext(Dispatchers.Main) {
                onDone(result.getOrNull()?.uploadId, result.exceptionOrNull())
            }
        }
    }

    fun downloadUploadPreview(sessionId: String, file: UploadAttachmentDto): ByteArray =
        (client ?: throw IOException(AppLocale.text("Нет подключения к серверу", "No server connection")))
            .downloadUploadPreview(sessionId, file)

    private fun addLocal(sessionId: String, item: TimelineItem) {
        val accumulator = synchronized(timelineLock) { timelines.getOrPut(sessionId) { SessionTimeline(sessionId) } }
        accumulator.addLocal(item)
        if (sessionId == selectedSessionId) {
            _timeline.value = accumulator.snapshot()
            _contextUsage.value = accumulator.contextSnapshot()
            _sessionPlan.value = accumulator.planSnapshot()
        }
    }

    fun decide(approval: ApprovalDto, decision: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return
        if (approval.kind != "user_input" && isApprovalAlreadyResolved(approval.taskId, approval.requestId)) {
            onDone(null)
            return
        }
        clearLastError()
        scope.launch {
            val result = if (approval.kind == "user_input") {
                runCatching {
                    active.answerUserInput(
                        approval.taskId,
                        approval.requestId,
                        UserInputRequest(
                            response = JsonObject(mapOf("answers" to JsonObject(emptyMap()))),
                            cancelled = decision == "reject",
                        ),
                    )
                }
            } else {
                runCatching { active.resolveApproval(approval.taskId, approval.requestId, decision) }
            }
            result.onFailure { handleFailure(it) }
            if (result.isSuccess && approval.kind != "user_input") {
                markResolvedLocally(approval.taskId, approval.requestId, decision, approval.sessionId.takeIf { it.isNotBlank() })
            }
            refreshApprovals()
            refreshSessions()
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun decideSudo(requestId: String, decision: String, password: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.resolveSudo(requestId, decision, password) }
            result.onFailure { handleFailure(it) }
            refreshApprovals()
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun decideTool(requestId: String, taskId: String, decision: String, onDone: (Throwable?) -> Unit) {
        val active = client ?: return
        if (isApprovalAlreadyResolved(taskId, requestId)) {
            onDone(null)
            return
        }
        clearLastError()
        scope.launch {
            val result = runCatching { active.resolveApproval(taskId, requestId, decision) }
            result.onFailure { handleFailure(it) }
            if (result.isSuccess) {
                markResolvedLocally(taskId, requestId, decision)
            }
            refreshApprovals()
            refreshSessions()
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    internal fun markResolvedLocally(taskId: String, requestId: String, decision: String, ownerSessionId: String? = null) {
        val sessionId = ownerSessionId ?: currentSessionIdFor(taskId)
        val event = BridgeEvent(
            kind = "approval.resolved",
            sessionId = sessionId,
            taskId = taskId,
            payload = buildJsonObject {
                put("request_id", JsonPrimitive(requestId))
                put("decision", JsonPrimitive(decision))
            },
        )
        voiceEvents.tryEmit(event)
        _approvals.update { items -> items.filterNot { it.taskId == taskId && it.requestId == requestId } }
        if (sessionId == null) return
        val accumulator = synchronized(timelineLock) { timelines.getOrPut(sessionId) { SessionTimeline(sessionId) } }
        accumulator.apply(event)
        if (sessionId == selectedSessionId) {
            _timeline.value = accumulator.snapshot()
            _contextUsage.value = accumulator.contextSnapshot()
            _sessionPlan.value = accumulator.planSnapshot()
        }
    }

    private fun currentSessionIdFor(taskId: String): String? =
        synchronized(timelineLock) {
            timelines.entries.firstOrNull { (_, accumulator) ->
                accumulator.snapshot().any { it is TimelineItem.ApprovalNotice && it.taskId == taskId }
            }?.key
        }

    internal fun isApprovalAlreadyResolved(taskId: String, requestId: String): Boolean {
        if (taskId.isBlank() || requestId.isBlank()) return false
        return synchronized(timelineLock) { timelines.values.any { it.isResolved(taskId, requestId) } }
    }

    fun cancelTask(taskId: String, mode: String, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        scope.launch {
            runCatching { active.cancelTask(taskId, mode) }
                .onFailure { handleFailure(it) }
                .also { result -> withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) } }
        }
    }

    fun cancelSession(sessionId: String, mode: String, onDone: (Throwable?) -> Unit = {}) {
        val active = client ?: return
        clearLastError()
        scope.launch {
            val result = runCatching { active.cancelSession(sessionId, mode) }
            result.onFailure { handleFailure(it) }
            if (result.isSuccess) {
                refreshSessions()
                if (selectedSessionId == sessionId) openSession(sessionId)
            }
            withContext(Dispatchers.Main) { onDone(result.exceptionOrNull()) }
        }
    }

    fun loadDevices() {
        val active = client ?: return
        scope.launch {
            runCatching { active.devices() }
                .onSuccess {
                    _devices.value = it.devices
                    _currentDeviceId.value = it.currentDeviceId
                }
                .onFailure { handleFailure(it) }
        }
    }

    fun revokeDevice(deviceId: String) {
        val active = client ?: return
        scope.launch {
            runCatching { active.revokeDevice(deviceId) }
                .onFailure { handleFailure(it) }
            loadDevices()
        }
    }

    // Hard deletes the device entry from the machine list (unlike revocation, which
    // only marks the access as revoked but keeps the line in the list).
    fun deleteDevice(deviceId: String) {
        val active = client ?: return
        scope.launch {
            runCatching { active.deleteDevice(deviceId) }
                .onFailure { handleFailure(it) }
            loadDevices()
        }
    }

    // Removes all devices with revoked access from the list at once.
    fun purgeRevokedDevices() {
        val active = client ?: return
        scope.launch {
            runCatching { active.purgeRevokedDevices() }
                .onFailure { handleFailure(it) }
            loadDevices()
        }
    }

    fun startStream() {
        val store = prefs ?: return
        if (!store.isPaired() || streamJob?.isActive == true) return
        val active = client ?: buildClient(store).also { client = it }
        streamJob = scope.launch {
            var attempt=0
            while(isActive && client===active) {
                if(!networkAvailable || store.token.isEmpty()) {
                    _connection.value="offline"
                    withTimeoutOrNull(30000) { networkWake.receive() }
                    continue
                }
                val closed=CompletableDeferred<Throwable?>()
                val owner="bridge-events:"+connectionScope(store.baseUrl)
                var ready=false
                _connection.value="connecting"
                val call=active.openEventStream(store.lastSeq,null,
                    onEvent={ event -> if(client===active && isActive) {
                        if(event.kind=="stream.ready") {
                            ready=true
                            streamReadyJob?.cancel()
                            streamReadyJob=scope.launch {
                                delay(ErrorCatalog.stableAfterMs)
                                if(client===active && !closed.isCompleted) {
                                    attempt=0
                                    recoverDiagnostic(owner,ErrorCatalog.stableAfterMs)
                                    refreshSessions();refreshApprovals();refreshApprovalSettings();refreshSecuritySettings();refreshSandboxSettings()
                                    selectedSessionId?.let { refreshSessionChanges(it) }
                                }
                            }
                        }
                        handleEvent(event)
                    } },onClosed={ closed.complete(it) })
                streamCall=call
                val error=try { closed.await() } finally { call.cancel() }
                streamReadyJob?.cancel()
                if(client!==active || !isActive)return@launch
                _connection.value="offline"
                val code=if(error==null)"STREAM.INTERRUPTED" else classifyDiagnostic(error,status=(error as? BridgeException)?.status)
                if(code=="AUTH.REJECTED" || code=="TLS.CERTIFICATE_REJECTED" || code=="TLS.PROTOCOL_FAILURE") {
                    recordDiagnostic(ErrorObservation(code=code,component="mobile-events",owner=owner,phase="connect"))
                    if(code=="AUTH.REJECTED") {
                        withContext(Dispatchers.Main) { ru.igorek.remote.service.VoiceCalls.close() }
                        _paired.value=false
                    }
                    break
                }
                val wait=reconnectDelay(attempt)
                attempt++
                recordDiagnostic(ErrorObservation(code=code,component="mobile-events",owner=owner,phase=if(ready)"sse_read" else "connect",
                    attempt=attempt,nextRetryAt=System.currentTimeMillis()+wait))
                withTimeoutOrNull(wait) { networkWake.receive() }
            }
        }
    }

    fun stopStream() {
        streamReadyJob?.cancel()
        streamCall?.cancel();streamCall=null
        streamJob?.cancel();streamJob=null
        _connection.value="offline"
    }

    private fun handleEvent(event: BridgeEvent) {
        val store = prefs
        val seq = event.seq
        if (event.kind == "stream.ready") {
            replayThrough = (event.payload["replay_through"] as? JsonPrimitive)?.longOrNull ?: 0L
            _connection.value = "online"
            selectedSessionId?.let { sessionId ->
                if (_sessionCheckpoints.value != null) refreshSessionCheckpoints(sessionId)
            }
        }

        if(event.kind in setOf("network.retry","network.recovered","task.finish","task.error")) {
            val error=event.payload["diagnostic"]?.let { runCatching { diagnosticJson.decodeFromString<ErrorObservation>(it.toString()) }.getOrNull() }
            if(error!=null) {
                if(event.kind=="network.recovered")recoverDiagnostic(error.owner,0,operationCompleted=true)
                else if(event.kind in setOf("network.retry","task.error") || event.payload.str("status")=="fail")recordDiagnostic(error)
            }
        }

        val sessionId = event.sessionId
        if (sessionId != null) {
            val accumulator = synchronized(timelineLock) {
                if (event.kind == "approval.resolved") timelines.getOrPut(sessionId) { SessionTimeline(sessionId) }
                else timelines[sessionId]
            }
            accumulator?.apply(event)
            if (sessionId == selectedSessionId && accumulator != null) {
                _timeline.value = accumulator.snapshot()
                _contextUsage.value = accumulator.contextSnapshot()
                _sessionPlan.value = accumulator.planSnapshot()
            }
        }
        if (store != null && seq != null && seq > store.lastSeq) store.lastSeq = seq
        if (shouldForwardVoiceEvent(event, replayThrough)) voiceEvents.tryEmit(event)
        if (seq != null && seq <= replayThrough) return


        if (event.kind.startsWith("subagent.")) selectedSessionId?.let { refreshAgentSessions(it) }
        when (event.kind) {
            "schedule.delivered" -> {
                refreshSessions()
                if (sessionId != null && sessionId == selectedSessionId) refreshFiles(sessionId)
            }
            "sudo.required" -> {
                alerts.tryEmit(Alert(
                    AppLocale.text("Нужно подтвердить sudo", "Sudo approval required"),
                    AppLocale.text("Открой подтверждение команд и проверь команду", "Open command approvals and review the command"),
                    null,
                ))
                refreshApprovals()
            }
            "sudo.resolved" -> refreshApprovals()
            "approval.required" -> {
                val tool = event.payload.str("tool")
                alerts.tryEmit(Alert(
                    AppLocale.text("Нужно подтверждение", "Approval required"),
                    AppLocale.text("Игорёк просит разрешение: $tool", "Igorek requests permission: $tool"),
                    sessionId,
                ))
                refreshApprovals()
                refreshSessions()
            }
            "task.status" -> {
                val status = event.payload.str("status")
                val taskId = event.payload.str("task_id")
                when (status) {
                    "processing" -> taskStartedAt[taskId] = System.currentTimeMillis()
                    "waiting_approval" -> alerts.tryEmit(Alert(
                        AppLocale.text("Нужно решение", "Decision required"),
                        AppLocale.text("Задача ждёт подтверждения", "Task is waiting for approval"),
                        sessionId,
                    ))
                    else -> Unit
                }
                refreshSessions()
            }
            "task.cancelled", "session.cancelled" -> {
                alerts.tryEmit(Alert(
                    AppLocale.text("Задача остановлена", "Task stopped"),
                    AppLocale.text("Сессия больше не выполняет эту задачу", "The session is no longer running this task"),
                    sessionId,
                ))
                refreshSessions()
            }
            "task.finish" -> {
                val status = event.payload.str("status")
                val reason = event.payload.str("reason")
                val taskId = event.payload.str("task_id")
                val startedAt = taskStartedAt.remove(taskId)
                val elapsed = if (startedAt != null) (System.currentTimeMillis() - startedAt) / 1000 else 0
                when {
                    reason == "interrupted" -> alerts.tryEmit(Alert(
                        AppLocale.text("Задача остановлена", "Task stopped"),
                        AppLocale.text("Остановка завершена", "Stop completed"),
                        sessionId,
                    ))
                    status == "fail" && ((event.payload["approval_blocked_count"] as? JsonPrimitive)?.longOrNull ?: 0L) > 0 -> Unit
                    status != "ok" -> alerts.tryEmit(Alert(
                        AppLocale.text("Задача завершилась с ошибкой", "Task failed"),
                        AppLocale.text("Статус: $status", "Status: $status"),
                        sessionId,
                    ))
                    elapsed >= LONG_TASK_SECONDS -> alerts.tryEmit(Alert(
                        AppLocale.text("Долгая задача завершена", "Long-running task completed"),
                        AppLocale.text("Игорёк закончил работу", "Igorek finished the work"),
                        sessionId,
                    ))
                }
                refreshSessions()
            }
            "approval.resolved" -> {
                refreshApprovals()
                refreshSessions()
            }
            "approval.settings_changed" -> refreshApprovalSettings()
            "security_level_changed" -> refreshSecuritySettings()
            "sandbox_settings_changed" -> refreshSandboxSettings()
            // File edits in the current session are immediately reflected in the header chip.
            "tool.result", "tool.request_batch" -> {
                if (sessionId != null && sessionId == selectedSessionId && isFileWriteToolEvent(event.kind, event.payload)) {
                    refreshSessionChanges(sessionId)
                    if (_sessionCheckpoints.value != null) refreshSessionCheckpoints(sessionId)
                }
            }
        }
    }

    private fun handleFailure(error: Throwable) {
        if(error is java.util.concurrent.CancellationException)return
        val diagnostic=when(error){is BridgeException->error.diagnostic;is DiagnosticIOException->error.diagnostic;else->null}
        if(diagnostic!=null) {
            val detail=if(error is BridgeException && !error.message.isNullOrBlank())"\n"+safeDiagnosticText(error.message.orEmpty()) else ""
            setLastError(errorMessage(diagnostic)+detail,if(diagnostic.code.startsWith("NET.") || diagnostic.code.startsWith("TLS."))BridgeErrorKind.CONNECTION else BridgeErrorKind.OPERATION,diagnostic)
            if(error is BridgeException && error.status==401)_paired.value=false
            return
        }
        if (error is BridgeException && error.status == 401) {
            _paired.value = false
            setLastError(AppLocale.text("Подключение не авторизовано. Проверьте вход.", "The connection is not authorized. Check sign-in."), BridgeErrorKind.OPERATION)
            return
        }
        val kind = bridgeErrorKind(error)
        val fallback=ErrorObservation(code=classifyDiagnostic(error),owner=client?.transportOwner.orEmpty(),nativeCode=error.javaClass.simpleName)
        setLastError(errorMessage(fallback),kind,fallback)
    }

    private fun setLastError(message: String, kind: BridgeErrorKind, diagnostic: ErrorObservation? = null) {
        synchronized(errorLock) {
            lastErrorKind = kind
            lastDiagnostic = diagnostic
            _lastError.value = message
            _notifications.value = mergeNotification(
                _notifications.value,
                message,
                kind.name,
                System.currentTimeMillis(),
                diagnostic,
            )
            persistNotifications()
        }
    }

    internal fun reportUiError(message: String, owner: String) {
        synchronized(errorLock) {
            val updated = mergeUiNotification(_notifications.value, message, owner, System.currentTimeMillis())
            if (updated != _notifications.value) {
                _notifications.value = updated
                persistNotifications()
            }
        }
    }

    fun markNotificationsRead() {
        synchronized(errorLock) {
            _notifications.value = _notifications.value.map { it.copy(read = true) }
            persistNotifications()
        }
    }

    fun clearNotifications() {
        synchronized(errorLock) { _notifications.value = emptyList();persistNotifications() }
    }

    private fun clearLastError() {
        synchronized(errorLock) {
            lastErrorKind = null
            lastDiagnostic = null
            _lastError.value = null
        }
    }

    private fun clearRecoveredConnectionError() {
        synchronized(errorLock) {
            if(httpReadySince==0L)httpReadySince=SystemClock.elapsedRealtime()
            val stable=SystemClock.elapsedRealtime()-httpReadySince
            if(stable<ErrorCatalog.stableAfterMs)return
            recoverDiagnostic(client?.transportOwner.orEmpty(),stable)
            if (shouldClearBridgeErrorAfterReconnect(lastErrorKind) && lastDiagnostic?.owner==client?.transportOwner && lastDiagnostic?.outcome!="unknown") {
                lastErrorKind = null
                _lastError.value = null
            }
        }
    }

    private fun recordDiagnostic(error: ErrorObservation) {
        val safe=sanitizeDiagnostic(error)
        if(safe.code=="OPERATION.CANCELLED")return
        synchronized(errorLock) {
            val age = if (resumedAt < 0) Long.MAX_VALUE else SystemClock.elapsedRealtime() - resumedAt
            val alreadyVisible = _notifications.value.any { !it.quiet && it.state != "recovered" &&
                it.diagnostic?.let(::diagnosticFingerprint) == diagnosticFingerprint(safe) }
            if (shouldQuietBackgroundTimeout(safe, activityVisible, age) && !alreadyVisible) {
                _notifications.value = mergeNotification(_notifications.value, errorMessage(safe),
                    BridgeErrorKind.CONNECTION.name, System.currentTimeMillis(), safe, quiet = true)
                persistNotifications()
                scheduleBackgroundTimeoutPromotion(safe.owner)
                return
            }
        }
        setLastError(errorMessage(safe),if(safe.code.startsWith("NET.") || safe.code.startsWith("TLS.") || safe.code.startsWith("STREAM."))BridgeErrorKind.CONNECTION else BridgeErrorKind.OPERATION,safe)
    }

    private fun recoverDiagnostic(owner:String,stableMs:Long,operationCompleted:Boolean=false) {
        synchronized(errorLock) {
            val updated=recoverNotifications(_notifications.value,owner,stableMs,System.currentTimeMillis(),operationCompleted)
            if(updated!=_notifications.value){_notifications.value=updated;persistNotifications()}
            if(lastDiagnostic?.owner==owner && lastDiagnostic?.outcome!="unknown" && (operationCompleted || stableMs>=ErrorCatalog.stableAfterMs)) {
                lastDiagnostic=null;lastErrorKind=null;_lastError.value=null
            }
        }
    }

    private fun persistNotifications() {
        runCatching {
            var values=_notifications.value.takeLast(NOTIFICATION_LIMIT)
            var encoded=diagnosticJson.encodeToString(values)
            while(encoded.length>131072 && values.size>1){values=values.drop(1);encoded=diagnosticJson.encodeToString(values)}
            prefs?.notificationHistory=encoded
        }
            .onFailure { notificationWriteFailures++ }
    }

    private fun loadNotificationHistory() {
        synchronized(errorLock) {
            _notifications.value=runCatching { diagnosticJson.decodeFromString<List<NotificationEntry>>(prefs?.notificationHistory ?: "[]")
                .takeLast(NOTIFICATION_LIMIT).map { it.copy(text=safeDiagnosticText(it.text),diagnostic=it.diagnostic?.let(::sanitizeDiagnostic),events=it.events.takeLast(8).map(::sanitizeDiagnostic),count=it.count.coerceIn(1,1000000000)) } }.getOrDefault(emptyList())
            pendingMessages.clear()
            runCatching { diagnosticJson.decodeFromString<Map<String,String>>(prefs?.pendingOperationKeys ?: "{}") }.getOrDefault(emptyMap()).entries.toList().takeLast(20).forEach { (hash,key) ->
                if(hash.matches(Regex("[a-f0-9]{24}")) && key.matches(Regex("[A-Za-z0-9_-]{8,160}")))pendingMessages[hash]=key
            }
        }
    }

    private fun persistPendingOperations() {
        runCatching { prefs?.pendingOperationKeys=diagnosticJson.encodeToString(pendingMessages.entries.toList().takeLast(20).associate { it.key to it.value }) }.onFailure { notificationWriteFailures++ }
    }

    fun diagnosticSnapshot(): String = synchronized(errorLock) {
        buildJsonObject {
            put("schema_version",JsonPrimitive(1));put("captured_at_ms",JsonPrimitive(System.currentTimeMillis()))
            put("version",JsonPrimitive(ru.igorek.remote.BuildConfig.VERSION_NAME));put("write_failures",JsonPrimitive(notificationWriteFailures))
            put("incidents",JsonArray(_notifications.value.mapNotNull { entry -> entry.diagnostic?.let { error -> buildJsonObject {
                put("incident_id",JsonPrimitive(entry.id));put("state",JsonPrimitive(entry.state));put("observations",JsonPrimitive(entry.count))
                put("read",JsonPrimitive(entry.read))
                put("recovered_at_ms",JsonPrimitive(entry.recoveredAt));put("recovery_proof",JsonPrimitive(entry.recoveryProof))
                put("first_seen_ms",JsonPrimitive(entry.firstSeen));put("last_seen_ms",JsonPrimitive(entry.lastSeen))
                put("error",diagnosticJson.parseToJsonElement(diagnosticJson.encodeToString(sanitizeDiagnostic(error))))
                put("events",diagnosticJson.parseToJsonElement(diagnosticJson.encodeToString(entry.events.takeLast(8).map(::sanitizeDiagnostic))))
                put("history_omitted",JsonPrimitive((entry.count-entry.events.size).coerceAtLeast(0)))
            } } }))
        }.toString()
    }
}

private fun JsonObject.str(key: String): String =
    (this[key] as? JsonPrimitive)?.contentOrNull ?: ""

private val FILE_WRITE_TOOLS = setOf("write", "edit", "apply_patch", "multiedit", "notebookedit")

private fun toolNameTouchesFiles(name: String): Boolean =
    name.substringAfterLast('.').trim().lowercase() in FILE_WRITE_TOOLS

internal fun isFileWriteToolEvent(kind: String, payload: JsonObject): Boolean = when (kind) {
    "tool.result" -> toolNameTouchesFiles(payload.str("tool_name"))
    "tool.request_batch" -> (payload["tools"] as? JsonArray)
        ?.any { tool ->
            toolNameTouchesFiles(((tool as? JsonObject)?.get("name") as? JsonPrimitive)?.contentOrNull.orEmpty())
        } == true
    else -> false
}
